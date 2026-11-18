from django.db import IntegrityError, transaction
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from common.exceptions import ApiError
from common.projects import ensure_owner, get_owned_project
from common.views import BaseAPIView

from . import compare as cmp
from . import params as cal
from . import participant
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
                  "error_before_pp": (r.basis or {}).get("mean_error"),
                  "error_after_pp": cmp.after_error(project, r)}
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


class CompareView(BaseAPIView):
    """GET /api/v1/projects/{id}/calibration/compare?revision=n — K03 보정 전·후 단계별 오차 (CAL-15)

    revision 을 빼면 현재 보정. 보정 후 실행이 없으면 status=waiting.
    """

    def get(self, request, project_id):
        project = get_owned_project(request.user, project_id)
        raw = request.query_params.get("revision")
        if raw is None:
            revision, _ = current(project)
        else:
            try:
                revision = int(raw)
            except ValueError:
                raise ApiError(422, "VALIDATION_ERROR", "revision 은 숫자입니다.", {"revision": ["숫자"]})
        rev = cmp.revision_or_404(project, revision)
        if rev is None:
            raise ApiError(404, "NOT_FOUND", "보정 기록을 찾을 수 없습니다.")
        return Response(cmp.compare(project, rev))


# ---------- K04 사람 관측 (CAL-16~18) ----------

def _observation_dict(row, predicted, names):
    from . import observations as obs
    stage_names = {s["id"]: s["name"] for s in (predicted or {}).get("stages", [])}
    return {"id": row.pk, "participant_code": row.participant_code, "task_id": row.task_id,
            "task_name": (predicted or {}).get("name", row.task_id), "outcome": row.outcome,
            "stage_id": row.stage_id or None, "stage_name": stage_names.get(row.stage_id) if row.stage_id else None,
            "cause": row.cause or None, "cause_name": names.get(row.cause) if row.cause else None,
            "pain_stage_id": row.pain_stage_id or None,
            "pain_stage_name": stage_names.get(row.pain_stage_id) if row.pain_stage_id else None,
            "pain_cause": row.pain_cause or None,
            "pain_cause_name": names.get(row.pain_cause) if row.pain_cause else None,
            "discomfort": row.discomfort,
            "purpose": row.purpose, "source": row.source, "updated_at": row.updated_at.isoformat(),
            "match": obs.match(row, predicted)}


def _observations_payload(project):
    from agent.behavior import CAUSES

    from . import observations as obs
    from .models import HumanObservation
    predicted, report = obs.predictions(project)
    names = {**CAUSES, "unknown": "원인 미상"}
    rows = [_observation_dict(r, predicted.get(r.task_id), names)
            for r in HumanObservation.objects.filter(project=project)]
    return {"project_id": project.pk, "report_id": str(report.id) if report else None,
            "tasks": [{"task_id": k, "name": v["name"], "stages": v["stages"],
                       "predicted": {"outcome": v["outcome"], "stage_id": v["stage_id"],
                                     "stage_name": v["stage_name"], "cause": v["cause"],
                                     "cause_name": names.get(v["cause"]) if v["cause"] else None,
                                     "pain_top": v["pain_top"]}}
                      for k, v in predicted.items()],
            "causes": [{"key": k, "name": v} for k, v in names.items()],
            "items": rows,
            # 평가용 관측만으로 맞춘 정도 (보정용과 섞지 않는다, CAL-18)
            "summary": obs.summary([r for r in rows if r["purpose"] == "evaluation"] or rows),
            "summary_basis": "evaluation" if any(r["purpose"] == "evaluation" for r in rows) else "all"}


class ObservationListView(BaseAPIView):
    """GET·POST /api/v1/projects/{id}/human-observations — 목록·예측과 일치 요약 / 저장(같은 참여자×과업은 덮어씀)"""

    def get(self, request, project_id):
        project = get_owned_project(request.user, project_id)
        return Response(_observations_payload(project))

    def post(self, request, project_id):
        from . import observations as obs
        from .models import HumanObservation
        project = get_owned_project(request.user, project_id)
        predicted, report = obs.predictions(project)
        if report is None:
            raise ApiError(409, "NO_PREDICTION", "먼저 사전 테스트를 한 번 실행해야 비교할 수 있어요.")
        data = obs.clean(project, request.data, predicted)
        row, created = HumanObservation.objects.update_or_create(
            project=project, participant_code=data["participant_code"], task_id=data["task_id"],
            defaults={**data, "source": HumanObservation.Source.MANUAL, "created_by": request.user})
        return Response({"replaced": not created, **_observations_payload(project)},
                        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class ObservationImportView(BaseAPIView):
    """POST /api/v1/projects/{id}/human-observations/import {task_id, purpose?, record} — recorder.js 결과 파일"""

    def post(self, request, project_id):
        from . import observations as obs
        from .models import HumanObservation
        project = get_owned_project(request.user, project_id)
        predicted, report = obs.predictions(project)
        if report is None:
            raise ApiError(409, "NO_PREDICTION", "먼저 사전 테스트를 한 번 실행해야 비교할 수 있어요.")
        task_id = request.data.get("task_id")
        if task_id not in predicted:
            raise ApiError(422, "VALIDATION_ERROR", "비교할 과업을 골라 주세요.", {"task_id": ["과업"]})
        draft = obs.from_recorder(request.data.get("record"), task_id,
                                  request.data.get("purpose") or "calibration", predicted)
        data = obs.clean(project, draft, predicted)
        row, created = HumanObservation.objects.update_or_create(
            project=project, participant_code=data["participant_code"], task_id=data["task_id"],
            defaults={**data, "source": HumanObservation.Source.RECORDER, "created_by": request.user})
        return Response({"replaced": not created, **_observations_payload(project)},
                        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class ObservationDetailView(BaseAPIView):
    """DELETE /api/v1/human-observations/{id}"""

    def delete(self, request, observation_id):
        from .models import HumanObservation
        row = HumanObservation.objects.select_related("project").filter(pk=observation_id).first()
        if row is None:
            raise ApiError(404, "NOT_FOUND", "관측을 찾을 수 없습니다.")
        ensure_owner(request.user, row.project)
        row.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------- 참여자 응답 링크 (PART-01~05) ----------

class ParticipantLinkListView(BaseAPIView):
    """GET·POST /api/v1/projects/{id}/participant-links — 담당자: 목록 / 만들기 {task_id, purpose?, days?, max_responses?}"""

    def get(self, request, project_id):
        from . import observations as obs
        from .models import ParticipantLink
        project = get_owned_project(request.user, project_id)
        predicted, _ = obs.predictions(project)
        links = ParticipantLink.objects.filter(project=project)[:50]
        return Response({"items": [participant.owner_dict(link, predicted) for link in links]})

    def post(self, request, project_id):
        from . import observations as obs
        project = get_owned_project(request.user, project_id)
        link = participant.create(project, request.user, request.data)
        return Response(participant.owner_dict(link, obs.predictions(project)[0]), status=status.HTTP_201_CREATED)


class ParticipantLinkDisableView(BaseAPIView):
    """POST /api/v1/participant-links/{id}/disable — 담당자: 링크 닫기 (응답 기록은 남는다)"""

    def post(self, request, link_id):
        from django.utils import timezone

        from .models import ParticipantLink
        link = ParticipantLink.objects.select_related("project").filter(pk=link_id).first()
        if link is None:
            raise ApiError(404, "NOT_FOUND", "링크를 찾을 수 없어요.")
        ensure_owner(request.user, link.project)
        if link.disabled_at is None:
            link.disabled_at = timezone.now()
            link.save(update_fields=["disabled_at"])
        return Response(participant.owner_dict(link))


class PublicParticipantView(BaseAPIView):
    """GET /api/v1/participant-links/{token} — 공개: 과업 안내·단계 캡처·이유 목록 (로그인 없음)"""
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request, token):
        return Response(participant.public_dict(participant.open_link(token)))


class PublicParticipantResponseView(BaseAPIView):
    """POST /api/v1/participant-links/{token}/responses — 공개: {client_id, outcome, pain_stage_id?, pain_cause?, discomfort?}"""
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request, token):
        link = participant.open_link(token)
        return Response(participant.submit(link, request.data), status=status.HTTP_201_CREATED)


class PublicParticipantScreenView(BaseAPIView):
    """GET /api/v1/participant-links/{token}/screens/{observation_id} — 공개: 그 과업 단계 캡처만"""
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request, token, observation_id):
        from runs.views import _ScreenshotView
        link = participant.open_link(token)
        return _ScreenshotView.serve(participant.screenshot_path(link, observation_id))
