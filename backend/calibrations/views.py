from django.db import IntegrityError, transaction
from rest_framework import status
from rest_framework.response import Response

from common.exceptions import ApiError
from common.projects import ensure_owner, get_owned_project
from common.views import BaseAPIView

from . import params as cal
from . import proposal
from .models import CalibrationRevision
from .services import current


def _revision_dict(rev):
    return {"id": rev.pk, "revision": rev.revision, "params": rev.params, "changes": rev.changes, "basis": rev.basis,
            "restored_from": rev.restored_from, "applied_at": rev.created_at.isoformat(),
            "applied_by": rev.created_by.get_username()}


def _summary(rev):
    if rev.restored_from is not None:
        return "기본 모형으로 되돌림" if rev.restored_from == 0 else f"r{rev.restored_from} 값으로 되돌림"
    return " · ".join(f"{c['segment_name']} {c['label']} {c['current']:.2f}→{c['proposed']:.2f}" for c in rev.changes)


def _comparison(basis):
    if not basis:
        return None
    seq = basis.get("run_sequence")
    return f"{f'{seq}차' if seq else '사전 테스트'} vs 실측 최근 {basis.get('days', 7)}일"


def _days(request):
    try:
        days = int(request.query_params.get("days", 7))
    except ValueError:
        days = 0
    if days not in (7, 14, 30):
        raise ApiError(422, "VALIDATION_ERROR", "days 는 7, 14, 30 중 하나입니다.", {"days": ["7, 14, 30"]})
    return days


def _expected(request, revision):
    expected = request.data.get("expected_revision")
    if isinstance(expected, bool) or not isinstance(expected, int):
        raise ApiError(422, "VALIDATION_ERROR", "expected_revision 이 필요합니다.", {"expected_revision": ["숫자"]})
    if expected != revision:
        raise ApiError(409, "REVISION_CONFLICT", "다른 보정이 먼저 적용됐어요. 새로고침한 뒤 다시 확인해 주세요.")


def _create(project, user, revision, **fields):
    try:
        with transaction.atomic():
            return CalibrationRevision.objects.create(project=project, revision=revision + 1, created_by=user, **fields)
    except IntegrityError as exc:       # 같은 순간 다른 적용이 먼저 들어감
        raise ApiError(409, "REVISION_CONFLICT", "다른 보정이 먼저 적용됐어요. 새로고침한 뒤 다시 확인해 주세요.") from exc


class CalibrationView(BaseAPIView):
    """GET /api/v1/projects/{id}/calibration — 현재 보정 (revision 0 = 기본 모형)"""

    def get(self, request, project_id):
        project = get_owned_project(request.user, project_id)
        latest = CalibrationRevision.objects.filter(project=project).select_related("created_by").first()
        if latest is None:
            return Response({"project_id": project.pk, "revision": 0, "params": {}, "applied_at": None,
                             "applied_by": None})
        return Response({"project_id": project.pk, **_revision_dict(latest)})


class ProposalView(BaseAPIView):
    """GET /api/v1/projects/{id}/calibration/proposal?days=7 — K01 조정안 (CAL-05~09)"""

    def get(self, request, project_id):
        project = get_owned_project(request.user, project_id)
        return Response(proposal.build(project, _days(request)))


class CalibrationListView(BaseAPIView):
    """GET·POST /api/v1/projects/{id}/calibrations — K03b 이력 / K02 적용

    POST {expected_revision, keys:[조정안 행 key], days?}: 서버가 조정안을 다시 만들어 고른 행만 반영한다
    (클라이언트가 보낸 숫자를 그대로 쓰지 않는다).
    """

    def get(self, request, project_id):
        project = get_owned_project(request.user, project_id)
        revisions = CalibrationRevision.objects.filter(project=project).select_related("created_by")[:50]
        items = [{**_revision_dict(r), "changes_summary": _summary(r), "base_comparison": _comparison(r.basis),
                  "error_before_pp": (r.basis or {}).get("mean_error"), "error_after_pp": None}
                 for r in revisions]
        return Response({"project_id": project.pk, "items": items, "next_cursor": None})

    def post(self, request, project_id):
        project = get_owned_project(request.user, project_id)
        revision, params = current(project)
        _expected(request, revision)
        keys = request.data.get("keys")
        if not isinstance(keys, list) or not keys or not all(isinstance(k, str) for k in keys):
            raise ApiError(422, "VALIDATION_ERROR", "적용할 조정안을 골라 주세요.", {"keys": ["하나 이상"]})
        days = request.data.get("days", 7)
        if days not in (7, 14, 30):
            raise ApiError(422, "VALIDATION_ERROR", "days 는 7, 14, 30 중 하나입니다.", {"days": ["7, 14, 30"]})
        built = proposal.build(project, days)
        by_key = {item["key"]: item for item in built["items"]}
        missing = sorted(set(keys) - set(by_key))
        if missing:
            raise ApiError(422, "PROPOSAL_CHANGED", "조정안이 바뀌었어요. 새로고침한 뒤 다시 골라 주세요.",
                           {"keys": [f"없는 조정안: {', '.join(missing)}"]})
        chosen = [by_key[k] for k in keys]
        try:
            new_params = proposal.apply_items(params, chosen)
        except cal.ParamsError as exc:
            raise ApiError(422, "VALIDATION_ERROR", str(exc), exc.fields) from exc
        rev = _create(project, request.user, revision, params=new_params, changes=chosen, basis=built["basis"] or {})
        return Response(_revision_dict(rev), status=status.HTTP_201_CREATED)


class RestoreView(BaseAPIView):
    """POST /api/v1/calibrations/{id}/restore {expected_revision} — 그 리비전 값을 복사한 새 리비전 (CAL-14)"""

    def post(self, request, revision_id):
        target = CalibrationRevision.objects.select_related("project").filter(pk=revision_id).first()
        if target is None:
            raise ApiError(404, "NOT_FOUND", "보정 기록을 찾을 수 없습니다.")
        project = ensure_owner(request.user, target.project)
        revision, _ = current(project)
        _expected(request, revision)
        rev = _create(project, request.user, revision, params=target.params, restored_from=target.revision)
        return Response(_revision_dict(rev), status=status.HTTP_201_CREATED)


class ResetView(BaseAPIView):
    """POST /api/v1/projects/{id}/calibrations/reset {expected_revision} — 기본 모형으로 되돌림 (새 리비전, 값 없음)"""

    def post(self, request, project_id):
        project = get_owned_project(request.user, project_id)
        revision, _ = current(project)
        _expected(request, revision)
        rev = _create(project, request.user, revision, params={"segments": {}}, restored_from=0)
        return Response(_revision_dict(rev), status=status.HTTP_201_CREATED)
