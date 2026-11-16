"""세션 상세 (타임라인·좌절 점수 그래프 데이터).

두 곳에서 같은 모양으로 읽는다.
- load_session_from_db: 백엔드 A의 Session·Observation·RunEvent (실제 실행). 스크린샷은 A의 서빙 주소
- load_session: 에이전트가 남긴 실행 폴더 (샘플 리포트용)
    {EXITPLAIN_RUN_OUTPUT_DIR}/{run_ref}_{task_id}_s{seed}/{attempt}/events.jsonl
"""
import json
import re
from pathlib import Path

from django.conf import settings

SAFE = re.compile(r"^[A-Za-z0-9_-]+$")


class SessionNotFound(LookupError):
    pass


def runs_root():
    return Path(getattr(settings, "EXITPLAIN_RUN_OUTPUT_DIR", settings.BASE_DIR.parent / "artifacts"))


def find_attempt(run_ref, task_id, seed, persona_id):
    """세션이 들어 있는 실행 폴더. 같은 실행을 다시 돌린 폴더가 여러 개면 가장 최근 것."""
    for value in (run_ref, task_id, persona_id):
        if not SAFE.fullmatch(str(value)):
            raise SessionNotFound("주소에 쓸 수 없는 문자가 있습니다.")
    run_dir = runs_root() / f"{run_ref}_{task_id}_s{int(seed)}"
    if not run_dir.is_dir():
        raise SessionNotFound("실행 결과 폴더가 없습니다.")
    attempts = [p for p in run_dir.iterdir()
                if (p / "events.jsonl").is_file() and (p / f"session_{persona_id}").exists()]
    if not attempts:
        attempts = [p for p in run_dir.iterdir() if (p / "events.jsonl").is_file()]
    if not attempts:
        raise SessionNotFound("실행 결과 파일이 없습니다.")
    return max(attempts, key=lambda p: (p / "events.jsonl").stat().st_mtime)


def _events(attempt_dir, session_id):
    events = []
    for line in (attempt_dir / "events.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            event = json.loads(line)
            if event.get("session_id") == session_id:
                events.append(event)
    return sorted(events, key=lambda e: e.get("sequence", 0))


def _summary(status, reason, steps, duration, terminal_stage, issues, timeline):
    from agent.behavior import CAUSES

    primary = next((i.get("category") for i in issues if i.get("primary")), None)
    threshold = next((s["frustration_threshold"] for s in reversed(timeline)
                      if s["frustration_threshold"] is not None), None)
    return {
        "status": status, "reason": reason, "steps": steps, "duration_seconds": duration,
        "terminal_stage": terminal_stage or (timeline[-1]["stage"] if timeline else None),
        "primary_cause": {"key": primary, "name": CAUSES.get(primary, primary)} if primary else None,
        "causes": [{"key": i.get("category"), "name": CAUSES.get(i.get("category"), i.get("category"))}
                   for i in issues if i.get("category")],
        "timeline": timeline,
        "chart": {"threshold": threshold,
                  "points": [{"index": s["index"], "stage": s["stage"], "score": s["frustration_score"]}
                             for s in timeline if s["frustration_score"] is not None]},
    }


def load_session_from_db(run, task_id, seed, persona_id):
    """A의 DB에서 세션 1개의 타임라인. 마찰·다음 행동은 RunEvent(friction.detected, action.completed)에서 붙인다."""
    from agent.behavior import CAUSES
    from agent.funnel import funnel_stage, stage_name
    from runs.models import RunEvent, Session

    session = (Session.objects.select_related("task_run").filter(run=run, task_id=task_id, seed=seed,
                                                                  persona_snapshot__code=persona_id).first())
    if session is None:
        raise SessionNotFound("이 실행에 해당 페르소나의 세션이 없습니다.")
    task_input = session.task_run.input_json or {}
    success_path, stage_paths = task_input.get("success_path"), task_input.get("stage_paths") or {}
    timeline, by_id = [], {}
    for o in session.observations.order_by("step"):
        stage = o.stage or funnel_stage(o.url, success_path, stage_paths)
        step = {"step_id": o.step_id, "index": len(timeline), "url": o.url, "stage": stage,
                "stage_name": stage_name(stage), "frustration_score": o.frustration_score,
                "frustration_threshold": o.frustration_threshold, "required_fields": o.required_fields,
                "screenshot": f"/api/v1/observations/{o.id}/screenshot" if o.artifact else None,   # A의 서빙 API
                "frictions": [], "action": None}
        timeline.append(step)
        by_id[o.step_id] = step
    for event in RunEvent.objects.filter(session=session, type__in=("friction.detected", "action.completed")):
        payload = event.payload or {}
        if event.type == "friction.detected":
            target = by_id.get((payload.get("evidence_step_ids") or [None])[0]) or (timeline[-1] if timeline else None)
            if target is not None:
                category = payload.get("category")
                target["frictions"].append({"category": category, "name": CAUSES.get(category, category),
                                            "contribution": payload.get("contribution")})
        else:
            target, action = by_id.get(payload.get("step_id")), payload.get("action") or {}
            if target is not None:
                target["action"] = {"kind": action.get("kind"), "target_id": action.get("target_id"),
                                    "explanation": action.get("explanation")}
    issues = [i for i in (session.issues or []) if isinstance(i, dict)]
    return {"session_id": session.session_key,
            "final_screenshot": f"/api/v1/sessions/{session.id}/final-screenshot" if session.final_screenshot else None,
            **_summary(session.status if session.is_terminal else "incomplete", session.reason, session.steps,
                       session.duration_seconds, session.terminal_stage, issues, timeline)}


def load_session(attempt_dir, persona_id):
    """한 세션의 타임라인. 화면(observation)마다 1줄, 그 화면에서 생긴 마찰과 다음 행동을 붙인다."""
    from agent.behavior import CAUSES
    from agent.funnel import funnel_stage, stage_name

    attempt_dir = Path(attempt_dir)
    session_id = f"session_{persona_id}"
    events = _events(attempt_dir, session_id)
    if not events:
        raise SessionNotFound("이 실행에 해당 페르소나의 세션이 없습니다.")
    run_input = json.loads((attempt_dir / "input.json").read_text(encoding="utf-8"))
    success_path, stage_paths = run_input.get("success_path"), run_input.get("stage_paths") or {}

    steps, by_id, finished = [], {}, None
    for event in events:
        kind, payload = event.get("type"), event.get("payload") or {}
        if kind == "observation.created":
            stage = payload.get("stage") or funnel_stage(payload.get("url", "/"), success_path, stage_paths)
            step = {"step_id": payload.get("step_id"), "index": len(steps), "url": payload.get("url"),
                    "stage": stage, "stage_name": stage_name(stage),
                    "frustration_score": payload.get("frustration_score"),
                    "frustration_threshold": payload.get("frustration_threshold"),
                    "required_fields": payload.get("required_fields"),
                    "screenshot": f"{session_id}/{payload.get('step_id')}.png",   # 실행 폴더 기준 경로 (서빙은 A)
                    "frictions": [], "action": None}
            steps.append(step)
            by_id[step["step_id"]] = step
        elif kind == "friction.detected":
            target = by_id.get((payload.get("evidence_step_ids") or [None])[0]) or (steps[-1] if steps else None)
            if target is not None:
                category = payload.get("category")
                target["frictions"].append({"category": category, "name": CAUSES.get(category, category),
                                            "contribution": payload.get("contribution")})
        elif kind == "action.completed":
            target = by_id.get(payload.get("step_id"))
            action = payload.get("action") or {}
            if target is not None:
                target["action"] = {"kind": action.get("kind"), "target_id": action.get("target_id"),
                                    "explanation": action.get("explanation")}
        elif kind == "session.finished":
            finished = payload

    finished = finished or {}
    issues = [i for i in (finished.get("issues") or []) if isinstance(i, dict)]
    return {"session_id": session_id, "final_screenshot": None,
            **_summary(finished.get("status", "incomplete"), finished.get("reason", "종료 이벤트 없음"),
                       finished.get("steps", len(steps)), finished.get("duration_seconds"),
                       finished.get("terminal_stage"), issues, steps)}