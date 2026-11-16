from django.http import HttpResponse
from rest_framework import serializers, status
from rest_framework.negotiation import BaseContentNegotiation
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from common.exceptions import ApiError
from common.projects import ensure_owner, get_owned_project
from common.views import BaseAPIView

from plans.serializers import StrictSerializer

from . import exporters, services
from .models import ReportSnapshot, ShareLink


class RunReportView(BaseAPIView):
    """GET /api/v1/runs/{run_ref}/reports — P12 리포트 (API_SPEC 8절). 가장 최근 revision과 이전 revision 목록."""

    def get(self, request, run_ref):
        latest = services.latest_for_run(run_ref)
        if latest is None:
            raise ApiError(404, "NOT_FOUND", "이 실행의 리포트가 아직 없습니다.")
        ensure_owner(request.user, latest.plan_snapshot.plan.project)
        history = ReportSnapshot.objects.filter(run_ref=run_ref).order_by("-revision").values(
            "id", "revision", "status", "created_at")
        return Response({**services.report_dict(latest),
                         "revisions": [{"report_id": str(h["id"]), "revision": h["revision"], "status": h["status"],
                                        "created_at": h["created_at"].isoformat()} for h in history]})


class ReportDetailView(BaseAPIView):
    """GET /api/v1/reports/{id} — 특정 revision 조회"""

    def get(self, request, report_id):
        report = ReportSnapshot.objects.select_related("plan_snapshot__plan__project").filter(pk=report_id).first()
        if report is None:
            raise ApiError(404, "NOT_FOUND", "리포트를 찾을 수 없습니다.")
        ensure_owner(request.user, report.plan_snapshot.plan.project)
        return Response(services.report_dict(report))


def _own_report(request, report_id):
    report = ReportSnapshot.objects.select_related("plan_snapshot__plan__project").filter(pk=report_id).first()
    if report is None:
        raise ApiError(404, "NOT_FOUND", "리포트를 찾을 수 없습니다.")
    ensure_owner(request.user, report.plan_snapshot.plan.project)
    return report


class ReportSessionListView(BaseAPIView):
    """GET /api/v1/reports/{id}/sessions?task_id=&seed=&status=&kind=&segment_id= — 세션 목록"""

    def get(self, request, report_id):
        report = _own_report(request, report_id)
        rows = services.session_rows(report, request.query_params)
        return Response({"report_id": str(report.id), "count": len(rows), "items": rows})


class ReportSessionDetailView(BaseAPIView):
    """GET /api/v1/reports/{id}/sessions/{task_id}/{seed}/{persona_id} — 세션 상세 (타임라인·좌절 점수)"""

    def get(self, request, report_id, task_id, seed, persona_id):
        report = _own_report(request, report_id)
        return Response(services.session_detail(report, task_id, seed, persona_id))


class ProjectReportListView(BaseAPIView):
    """GET /api/v1/projects/{id}/reports — 진단 기록 목록 (실행마다 최신 리포트 1개)"""

    def get(self, request, project_id):
        project = get_owned_project(request.user, project_id)
        items = [services.report_summary(r) for r in services.project_reports(project)]
        return Response({"project_id": project.pk, "count": len(items), "items": items})


class ProjectDashboardView(BaseAPIView):
    """GET /api/v1/projects/{id}/dashboard — 대시보드 (최근 실행 요약, 달성률 추이, 주요 이탈 원인)"""

    def get(self, request, project_id):
        return Response(services.dashboard(get_owned_project(request.user, project_id)))


# ---------- 내보내기 (11/16 Markdown, 11/17 PDF) ----------

class AnyAcceptNegotiation(BaseContentNegotiation):
    """파일 요청의 Accept(text/markdown, application/pdf)가 JSON 렌더러와 맞지 않아 406이 나지 않게 한다."""

    def select_parser(self, request, parsers):
        return parsers[0]

    def select_renderer(self, request, renderers, format_suffix=None):
        return renderers[0], renderers[0].media_type


def _download(content, content_type, filename):
    response = HttpResponse(content, content_type=content_type)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


class ReportMarkdownView(BaseAPIView):
    """GET /api/v1/reports/{id}/export.md — Markdown 파일 내려받기"""
    content_negotiation_class = AnyAcceptNegotiation

    def get(self, request, report_id):
        report = _own_report(request, report_id)
        return _download(exporters.report_markdown(report).encode("utf-8"), "text/markdown; charset=utf-8",
                         exporters.export_filename(report, "md"))


class ReportPdfView(BaseAPIView):
    """GET /api/v1/reports/{id}/export.pdf — PDF 파일 내려받기 (Chromium으로 인쇄, 1~3초)"""
    content_negotiation_class = AnyAcceptNegotiation

    def get(self, request, report_id):
        report = _own_report(request, report_id)
        page = exporters.markdown_to_html(exporters.report_markdown(report))
        try:
            pdf = exporters.render_pdf(page)
        except exporters.PdfUnavailable as exc:
            raise ApiError(503, "PDF_UNAVAILABLE", "지금은 PDF를 만들 수 없습니다. Markdown으로 내려받아 주세요.") from exc
        return _download(pdf, "application/pdf", exporters.export_filename(report, "pdf"))


# ---------- 공유 링크 (11/17) ----------

class ShareLinkCreateSerializer(StrictSerializer):
    expires_in_days = serializers.IntegerField(min_value=1, max_value=services.SHARE_DAYS_MAX, required=False,
                                               default=services.SHARE_DAYS_DEFAULT)


class ReportShareLinkView(BaseAPIView):
    """GET·POST /api/v1/reports/{id}/share-links — 공유 링크 목록·만들기 {expires_in_days: 1~30, 기본 7}"""

    def get(self, request, report_id):
        report = _own_report(request, report_id)
        return Response({"items": [services.share_link_dict(link) for link in report.share_links.all()]})

    def post(self, request, report_id):
        report = _own_report(request, report_id)
        serializer = ShareLinkCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        link = services.create_share_link(report, request.user, serializer.validated_data["expires_in_days"])
        return Response(services.share_link_dict(link), status=status.HTTP_201_CREATED)


class ShareLinkDetailView(BaseAPIView):
    """DELETE /api/v1/share-links/{id} — 공유 링크 취소"""

    def delete(self, request, link_id):
        link = ShareLink.objects.select_related("report__plan_snapshot__plan__project").filter(pk=link_id).first()
        if link is None:
            raise ApiError(404, "NOT_FOUND", "공유 링크를 찾을 수 없습니다.")
        ensure_owner(request.user, link.report.plan_snapshot.plan.project)
        services.revoke_share_link(link)
        return Response(status=status.HTTP_204_NO_CONTENT)


class SharedReportView(BaseAPIView):
    """GET /api/v1/shared/{token} — 로그인 없이 공유된 리포트 보기"""
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request, token):
        return Response(services.shared_report(token))