"""에이전트가 남긴 events.jsonl / summary.json 을 DB(RunEvent·Session·Observation)로 가져온다.

- 같은 줄을 두 번 읽어도 중복이 생기지 않는다 (RunEvent.event_id 가 유일).
- 아직 쓰는 중인 마지막 줄(개행 없음)은 건드리지 않는다.
- 읽은 위치(TaskRun.events_offset)는 DB 저장이 성공한 뒤에만 앞으로 옮긴다. 도중에 죽어도 다시 읽으면 된다.
- 에이전트 코드는 수정하지 않는다. 형식은 agent/runtime.py 의 emit() 과 SessionResult 를 따른다.
"""
import json
import logging
import re
from pathlib import Path

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import Observation, RunEvent, Session, TaskRun

logger = logging.getLogger(__name__)

STEP_NUMBER = re.compile(r"(\d+)$")
READ_LIMIT_BYTES = 8_000_000  # 한 번에 읽는 최대 크기. 남으면 다음 호출에서 이어 읽는다


def artifact_root():
    return Path(settings.ARTIFACT_ROOT).resolve()


def relative_artifact(path_text):
    """에이전트가 적은 절대경로를 ARTIFACT_ROOT 기준 상대경로로 바꾼다. 루트 밖이면 빈 문자열(서빙하지 않음)."""
    if not path_text:
        return ""
    try:
        return Path(path_text).resolve().relative_to(artifact_root()).as_posix()
    except (ValueError, OSError):
        return ""


def _events_file(task_run):
    return Path(task_run.output_dir) / "events.jsonl" if task_run.output_dir else None


def _record_problem(task_run, code, message):
    if not task_run.error_code:
        TaskRun.objects.filter(pk=task_run.pk).update(error_code=code, error_message=message[:500])
        task_run.error_code, task_run.error_message = code, message[:500]


class _Batch:
    """한 번의 가져오기 동안 세션을 캐시한다."""

    def __init__(self, task_run):
        self.task_run = task_run
        self.run = task_run.run
        self.sessions = {s.session_key: s for s in Session.objects.filter(task_run=task_run)}
        self._personas = None

    def persona(self, code):
        if self._personas is None:
            from personas.models import PersonaSnapshot
            self._personas = {p.code: p for p in PersonaSnapshot.objects.filter(plan_snapshot=self.run.plan_snapshot)}
        return self._personas.get(code)


def _occurred_at(event):
    return parse_datetime(event.get("occurred_at") or "") or timezone.now()


def _on_session_started(batch, sid, payload, when):
    code = (payload.get("persona") or {}).get("id") or sid.removeprefix("session_")
    persona = batch.persona(code)
    if persona is None:
        _record_problem(batch.task_run, "UNKNOWN_PERSONA", f"확정된 계획에 없는 페르소나입니다: {code}")
        return None
    session, _ = Session.objects.get_or_create(
        task_run=batch.task_run, session_key=sid,
        defaults={"run": batch.run, "task_id": batch.task_run.task_key, "seed": batch.task_run.seed,
                  "persona_snapshot": persona, "started_at": when})
    batch.sessions[sid] = session
    return session


def _on_observation(session, payload, when):
    step_id = str(payload.get("step_id") or "")
    match = STEP_NUMBER.search(step_id)
    if session is None or not match:
        return
    step = int(match.group(1))
    Observation.objects.get_or_create(session=session, step=step, defaults={
        "step_id": step_id[:20], "url": str(payload.get("url") or "")[:1000],
        "artifact": relative_artifact(payload.get("artifact")), "required_fields": int(payload.get("required_fields") or 0),
        "text": str(payload.get("text") or ""), "stage": str(payload.get("stage") or "")[:40],
        "frustration_score": _number(payload.get("frustration_score")),
        "frustration_threshold": _number(payload.get("frustration_threshold")), "occurred_at": when})
    changed = []
    if not session.is_terminal and session.steps < step + 1:
        session.steps, changed = step + 1, ["steps"]
    score = _number(payload.get("frustration_score"))
    if score is not None and score > session.max_frustration:
        session.max_frustration = score
        changed.append("max_frustration")
    if changed:
        session.save(update_fields=changed)


def _number(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _on_friction(session, payload):
    score = _number(payload.get("frustration_score"))
    if session is not None and score is not None and score > session.max_frustration:
        session.max_frustration = score
        session.save(update_fields=["max_frustration"])


def _on_session_finished(session, payload, when):
    if session is None:
        return
    status = payload.get("status")
    session.status = status if status in Session.Status.values and status != Session.Status.RUNNING \
        else Session.Status.AGENT_FAILURE
    session.reason = str(payload.get("reason") or "")
    session.policy = str(payload.get("policy") or "")[:60]
    session.model_id = str(payload.get("model_id") or "")[:100]
    session.steps = int(payload.get("steps") or session.steps)
    session.duration_seconds = float(payload.get("duration_seconds") or 0)
    session.model_calls = int(payload.get("model_calls") or 0)
    session.input_tokens = int(payload.get("input_tokens") or 0)
    session.output_tokens = int(payload.get("output_tokens") or 0)
    session.issues = payload.get("issues") if isinstance(payload.get("issues"), list) else []
    session.final_screenshot = relative_artifact(payload.get("final_artifact"))
    session.terminal_stage = str(payload.get("terminal_stage") or "")[:80]
    stages = payload.get("stages_reached")
    session.stages_reached = [str(s) for s in stages] if isinstance(stages, list) else []
    session.finished_at = when
    session.save()


def _apply(batch, event):
    kind, sid = event["type"], event.get("session_id") or ""
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    when = _occurred_at(event)
    if kind == "session.started":
        session = _on_session_started(batch, sid, payload, when)
    else:
        session = batch.sessions.get(sid)
        if kind == "observation.created":
            _on_observation(session, payload, when)
        elif kind == "friction.detected":
            _on_friction(session, payload)
        elif kind == "session.finished":
            _on_session_finished(session, payload, when)
    RunEvent.objects.create(run=batch.run, task_run=batch.task_run, session=session, event_id=str(event["event_id"])[:40],
                            sequence=int(event.get("sequence") or 0), type=kind[:40], payload=payload, occurred_at=when)


def import_new_events(task_run):
    """아직 읽지 않은 완성된 줄을 가져온다. 가져온 이벤트 수를 돌려준다."""
    path = _events_file(task_run)
    if path is None or not path.is_file():
        return 0
    with path.open("rb") as f:
        f.seek(task_run.events_offset)
        chunk = f.read(READ_LIMIT_BYTES)
    end = chunk.rfind(b"\n")
    if end < 0:
        return 0  # 쓰는 중인 줄뿐이다
    complete = chunk[: end + 1]
    events = []
    for number, raw in enumerate(complete.splitlines(), 1):
        if not raw.strip():
            continue
        try:
            event = json.loads(raw)
            if not isinstance(event, dict) or not event.get("event_id") or not event.get("type"):
                raise ValueError("event_id/type 없음")
            events.append(event)
        except ValueError:
            logger.warning("events.jsonl 줄을 건너뜀 (task_run=%s, offset=%s, 줄=%s)", task_run.pk, task_run.events_offset, number)
            _record_problem(task_run, "BAD_EVENT_LINE", "events.jsonl 에 읽을 수 없는 줄이 있었습니다.")
    seen = set(RunEvent.objects.filter(event_id__in=[str(e["event_id"])[:40] for e in events]).values_list("event_id", flat=True))
    batch = _Batch(task_run)
    imported = 0
    with transaction.atomic():
        for event in events:
            if str(event["event_id"])[:40] in seen:
                continue
            try:
                with transaction.atomic():  # 이벤트 하나가 잘못돼도 나머지는 가져온다
                    _apply(batch, event)
                imported += 1
            except (IntegrityError, ValueError, TypeError):
                logger.exception("이벤트를 가져오지 못함 (task_run=%s, event_id=%s)", task_run.pk, event.get("event_id"))
                _record_problem(task_run, "BAD_EVENT", "일부 이벤트를 가져오지 못했습니다.")
        TaskRun.objects.filter(pk=task_run.pk).update(events_offset=task_run.events_offset + len(complete))
    task_run.events_offset += len(complete)
    return imported


def drain_events(task_run):
    """파일 끝까지 모두 가져온다 (프로세스가 끝난 뒤에 쓴다)."""
    path = _events_file(task_run)
    while path is not None and path.is_file() and task_run.events_offset < path.stat().st_size:
        before = task_run.events_offset
        import_new_events(task_run)
        if task_run.events_offset == before:  # 개행 없이 끝난 마지막 줄 등 더 읽을 수 없다
            break


def finalize_task_run(task_run, *, exit_code=None, cancelled=False, error_code="", error_message=""):
    """프로세스가 끝난 뒤 마무리한다: 남은 이벤트·summary 가져오기, 끝나지 않은 세션 정리, TaskRun 상태 확정.

    종료 코드는 판단 기준이 아니다. agent.cli 는 기술 실패(site_failure 등)가 있어도 1 로 끝나므로,
    summary.json 이 남았으면 프로세스는 정상 종료로 본다.
    """
    drain_events(task_run)
    summary = None
    summary_file = Path(task_run.output_dir) / "summary.json" if task_run.output_dir else None
    if summary_file is not None and summary_file.is_file():
        try:
            summary = json.loads(summary_file.read_text(encoding="utf-8"))
        except ValueError:
            error_code, error_message = error_code or "BAD_SUMMARY", error_message or "summary.json 을 읽을 수 없습니다."

    now = timezone.now()
    dangling = Session.objects.filter(task_run=task_run, status=Session.Status.RUNNING)
    if cancelled:
        dangling.update(status=Session.Status.CANCELLED, reason="사용자가 실행을 취소했습니다.", finished_at=now)
    else:
        dangling.update(status=Session.Status.AGENT_FAILURE, finished_at=now,
                        reason="에이전트가 결과를 남기지 않고 종료되었습니다.")

    if cancelled:
        task_run.status = TaskRun.Status.CANCELLED
    elif summary is not None:
        task_run.status = TaskRun.Status.SUCCEEDED
    else:
        task_run.status = TaskRun.Status.FAILED
        error_code = error_code or "AGENT_CRASHED"
        error_message = error_message or f"에이전트가 summary.json 없이 종료되었습니다 (종료 코드 {exit_code})."
    task_run.summary = summary
    task_run.exit_code = exit_code
    task_run.finished_at = now
    if error_code and not task_run.error_code:
        task_run.error_code, task_run.error_message = error_code, error_message[:500]
    task_run.save(update_fields=["status", "summary", "exit_code", "finished_at", "error_code", "error_message"])
    return task_run
