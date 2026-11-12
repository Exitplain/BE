from rest_framework.response import Response

from common.exceptions import ApiError
from common.projects import ensure_owner
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