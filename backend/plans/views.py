from rest_framework import status
from rest_framework.response import Response

from common.exceptions import ApiError
from common.projects import ensure_owner, get_owned_project
from common.views import BaseAPIView

from . import services
from .models import PlanSnapshot, TestPlan
from .run_input import UNSUPPORTED_FIELDS, RunInputError, build_run_inputs
from .serializers import ConfirmationSerializer, PlanCreateSerializer, PlanPatchSerializer


def _own_plan(request, plan_id):
    plan = TestPlan.objects.select_related("project", "discovery").filter(pk=plan_id).first()
    if plan is None:
        raise ApiError(404, "NOT_FOUND", "계획을 찾을 수 없습니다.")
    ensure_owner(request.user, plan.project)
    return plan


def _own_snapshot(request, snapshot_id):
    snapshot = PlanSnapshot.objects.select_related("plan__project").filter(pk=snapshot_id).first()
    if snapshot is None:
        raise ApiError(404, "NOT_FOUND", "확정된 계획을 찾을 수 없습니다.")
    ensure_owner(request.user, snapshot.plan.project)
    return snapshot


def _plan_response(plan, status_code=200):
    response = Response(services.plan_dict(plan), status=status_code)
    response["ETag"] = f'"{plan.revision}"'
    return response


class ProjectPlanCreateView(BaseAPIView):
    """POST /api/v1/projects/{project_id}/plans  {discovery_id} — P05 진입"""

    def post(self, request, project_id):
        project = get_owned_project(request.user, project_id)
        serializer = PlanCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        plan = services.create_plan(request.user, project, serializer.validated_data["discovery_id"])
        return _plan_response(plan, status.HTTP_201_CREATED)


class PlanDetailView(BaseAPIView):
    """GET /api/v1/plans/{id}  ·  PATCH /api/v1/plans/{id} (If-Match: "revision") — P05·P05b"""

    def get(self, request, plan_id):
        return _plan_response(_own_plan(request, plan_id))

    def patch(self, request, plan_id):
        plan = _own_plan(request, plan_id)
        revision = services.parse_if_match(request.headers.get("If-Match"))
        serializer = PlanPatchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return _plan_response(services.update_plan(plan.pk, revision, serializer.validated_data))


class FunnelReestimateView(BaseAPIView):
    """POST /api/v1/plans/{id}/task-funnels/{task_id}/reestimate (If-Match) — P05b '다시 추정'"""

    def post(self, request, plan_id, task_id):
        plan = _own_plan(request, plan_id)
        revision = services.parse_if_match(request.headers.get("If-Match"))
        return _plan_response(services.reestimate_funnel(plan.pk, revision, task_id))


class PlanConfirmationView(BaseAPIView):
    """POST /api/v1/plans/{id}/confirmations  {revision} — P05c 확정 → P06 페르소나 생성"""

    def post(self, request, plan_id):
        plan = _own_plan(request, plan_id)
        serializer = ConfirmationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        snapshot = services.confirm_plan(plan.pk, serializer.validated_data["revision"], request.user)
        return Response(services.snapshot_dict(snapshot), status=status.HTTP_201_CREATED)


class PlanRevisionDetailView(BaseAPIView):
    """GET /api/v1/plan-revisions/{id} — 확정 스냅샷 조회"""

    def get(self, request, snapshot_id):
        return Response(services.snapshot_dict(_own_snapshot(request, snapshot_id)))


class PlanRevisionRunInputsView(BaseAPIView):
    """GET /api/v1/plan-revisions/{id}/run-inputs — 에이전트 입력 미리보기 (개발 확인용)"""

    def get(self, request, snapshot_id):
        snapshot = _own_snapshot(request, snapshot_id)
        try:
            inputs = build_run_inputs(snapshot, run_id="preview")
        except RunInputError as exc:
            raise ApiError(409, "RUN_INPUT_UNAVAILABLE", str(exc))
        return Response({"items": inputs, "not_passed_to_agent": list(UNSUPPORTED_FIELDS)})