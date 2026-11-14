from rest_framework.response import Response

from common.exceptions import ApiError
from common.projects import ensure_owner, get_owned_project
from common.views import BaseAPIView

from . import services
from .models import ReportSnapshot


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