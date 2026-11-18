"""참여자 응답 링크 (PART-01~05). 로그인 없는 공개 페이지가 쓰는 API 와 담당자 API.

- 공개 응답에는 그 과업의 단계 이름·캡처만 담는다. 예측 수치·다른 과업은 보여 주지 않는다 (PART-04)
- 질문은 모두 선택형이다. 자유 입력·이름·연락처가 없다 (PART-03)
- 응답은 사람 관측(source=participant_link)으로 저장한다. 참여자 코드는 R-001… 로 자동 (PART-05)
"""
import secrets
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from agent.behavior import CAUSES
from common.exceptions import ApiError

from . import observations as obs
from .models import HumanObservation, ParticipantLink, ParticipantSubmission

DEFAULT_DAYS = 14
MAX_DAYS = 60
MAX_RESPONSES = 200

# 참여자에게 보여 줄 쉬운 문장 → 모델 원인 (PART-03)
REASONS = [
    ("navigation", "뭘 해야 할지 헷갈렸어요"),
    ("input_burden", "입력할 게 너무 많았어요"),
    ("trust", "개인정보를 먼저 물어봐서 불안했어요"),
    ("cost", "생각보다 비싸거나 갑자기 돈이 붙었어요"),
    ("value", "가격이나 내용이 잘 안 보였어요"),
    ("technical", "느리거나 오류가 났어요"),
    ("accessibility", "버튼이 작거나 누르기 어려웠어요"),
    ("unknown", "잘 모르겠어요"),
]
assert {k for k, _ in REASONS} - {"unknown"} == set(CAUSES)


def link_state(link):
    if link.disabled_at:
        return "disabled"
    if link.expires_at <= timezone.now():
        return "expired"
    if link.responses >= link.max_responses:
        return "full"
    return "open"


def _int_in(value, low, high):
    return not isinstance(value, bool) and isinstance(value, int) and low <= value <= high


def create(project, user, body):
    predicted, report = obs.predictions(project)
    if report is None:
        raise ApiError(409, "NO_PREDICTION", "먼저 사전 테스트를 한 번 실행해야 링크를 만들 수 있어요.")
    if not isinstance(body, dict):
        raise ApiError(422, "VALIDATION_ERROR", "형식이 올바르지 않아요.")
    task_id = body.get("task_id")
    if task_id not in predicted:
        raise ApiError(422, "VALIDATION_ERROR", "과업을 골라 주세요.", {"task_id": ["과업"]})
    purpose = body.get("purpose") or HumanObservation.Purpose.CALIBRATION
    if purpose not in HumanObservation.Purpose.values:
        raise ApiError(422, "VALIDATION_ERROR", "보정용 또는 평가용을 골라 주세요.", {"purpose": ["용도"]})
    days = body.get("days", DEFAULT_DAYS)
    limit = body.get("max_responses", 50)
    if not _int_in(days, 1, MAX_DAYS):
        raise ApiError(422, "VALIDATION_ERROR", f"기한은 1~{MAX_DAYS}일이에요.", {"days": [f"1~{MAX_DAYS}"]})
    if not _int_in(limit, 1, MAX_RESPONSES):
        raise ApiError(422, "VALIDATION_ERROR", f"응답 상한은 1~{MAX_RESPONSES}명이에요.",
                       {"max_responses": [f"1~{MAX_RESPONSES}"]})
    return ParticipantLink.objects.create(project=project, created_by=user, token=secrets.token_urlsafe(16),
                                          task_id=task_id, purpose=purpose, max_responses=limit,
                                          expires_at=timezone.now() + timedelta(days=days))


def owner_dict(link, predicted=None):
    task = (predicted or {}).get(link.task_id) or {}
    return {"id": link.pk, "token": link.token, "path": f"/r/{link.token}", "task_id": link.task_id,
            "task_name": task.get("name", link.task_id), "purpose": link.purpose, "state": link_state(link),
            "responses": link.responses, "max_responses": link.max_responses,
            "expires_at": link.expires_at.isoformat(), "created_at": link.created_at.isoformat()}


def open_link(token):
    link = ParticipantLink.objects.select_related("project", "created_by").filter(token=token).first()
    if link is None:
        raise ApiError(404, "NOT_FOUND", "링크를 찾을 수 없어요.")
    state = link_state(link)
    if state != "open":
        message = {"disabled": "이 링크는 닫혔어요.", "expired": "이 링크는 기한이 지났어요.",
                   "full": "응답이 모두 모였어요. 참여해 주셔서 감사해요."}[state]
        raise ApiError(410, f"LINK_{state.upper()}", message)
    return link


def public_dict(link):
    predicted, _ = obs.predictions(link.project)
    task = predicted.get(link.task_id)
    if task is None:
        raise ApiError(410, "LINK_DISABLED", "이 링크의 과업이 더 이상 없어요.")
    shots = task.get("shots") or {}
    return {
        "project_name": link.project.name,
        "site_url": link.project.entry_url,
        "task_name": task["name"],
        "screens": [{"id": s["id"], "name": s["name"],
                     "screenshot_url": f"/api/v1/participant-links/{link.token}/screens/{shots[s['id']]}"
                     if s["id"] in shots else None} for s in task["stages"]],
        "reasons": [{"key": key, "label": label} for key, label in REASONS],
        "expires_at": link.expires_at.isoformat(),
    }


def screenshot_path(link, observation_id):
    """이 링크 과업의 단계 캡처일 때만 파일 경로를 돌려준다 (다른 캡처는 404)."""
    from runs.models import Observation
    predicted, _ = obs.predictions(link.project)
    allowed = set(((predicted.get(link.task_id) or {}).get("shots") or {}).values())
    row = Observation.objects.filter(pk=observation_id).first() if observation_id in allowed else None
    if row is None:
        raise ApiError(404, "NOT_FOUND", "화면을 찾을 수 없어요.")
    return row.artifact


def submit(link, body):
    allowed = {"client_id", "outcome", "pain_stage_id", "pain_cause", "discomfort"}
    if not isinstance(body, dict) or set(body) - allowed:
        raise ApiError(422, "VALIDATION_ERROR", "응답 형식이 올바르지 않아요.")
    client_id = body.get("client_id")
    if not isinstance(client_id, str) or not 8 <= len(client_id) <= 64:
        raise ApiError(422, "VALIDATION_ERROR", "응답 형식이 올바르지 않아요.", {"client_id": ["8~64자"]})
    outcome = body.get("outcome")
    pain_stage = body.get("pain_stage_id") or ""
    pain_cause = body.get("pain_cause") or ""
    if outcome == "dropout" and not pain_stage:
        raise ApiError(422, "VALIDATION_ERROR", "그만둔 화면을 골라 주세요.", {"pain_stage_id": ["화면"]})
    predicted, _ = obs.predictions(link.project)
    with transaction.atomic():
        locked = ParticipantLink.objects.select_for_update().get(pk=link.pk)
        if link_state(locked) != "open":
            raise ApiError(410, "LINK_FULL", "응답이 모두 모였어요. 참여해 주셔서 감사해요.")
        data = obs.clean(link.project, {
            "participant_code": f"R-{locked.responses + 1:03d}", "task_id": link.task_id, "purpose": link.purpose,
            "outcome": outcome,
            # 그만둔 참여자는 고른 화면을 그만둔 화면으로도 쓴다 (원인은 고른 이유, 없으면 미상)
            "stage_id": pain_stage if outcome == "dropout" else "",
            "cause": (pain_cause or "unknown") if outcome == "dropout" else "",
            "pain_stage_id": pain_stage, "pain_cause": pain_cause, "discomfort": body.get("discomfort"),
        }, predicted)
        try:
            with transaction.atomic():
                submission = ParticipantSubmission.objects.create(link=locked, client_id=client_id)
        except IntegrityError as exc:
            raise ApiError(409, "ALREADY_SUBMITTED", "이미 답해 주셨어요. 감사해요!") from exc
        row = HumanObservation.objects.create(project=link.project, created_by=link.created_by,
                                              source=HumanObservation.Source.PARTICIPANT_LINK, **data)
        submission.observation = row
        submission.save(update_fields=["observation"])
        ParticipantLink.objects.filter(pk=locked.pk).update(responses=F("responses") + 1)
    return {"participant_code": row.participant_code}
