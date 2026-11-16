"""실행 결과를 '세션 기록' 목록으로 바꾸는 곳. 집계 코드는 이 목록만 본다.

세션 기록 1개 모양:
    {"task_id": "order", "seed": 1, "persona_id": "P-001",
     "status": "success" | "simulated_dropout" | "agent_failure" | ... | "incomplete",
     "reason": "...", "steps": 7, "duration_seconds": 12.3,
     "stages": ["landing", "detail", ...],      # 화면마다 퍼널 단계 (agent/funnel.py 7단계 키), 순서대로
     "causes": ["input_burden", "trust"],       # 세션에서 기록된 이탈 원인 (agent/behavior.py CAUSES 키)
     "primary_cause": "input_burden" | None}    # 가상 이탈을 일으킨 주 원인

두 가지로 만든다.
- load_from_db(run): 백엔드 A의 Session·Observation (실제 실행)
- load_from_run_dir(root): 에이전트가 남긴 파일(input.json, events.jsonl) (샘플·확인용)
예전 에이전트 기록(observation에 stage가 없음)은 화면 경로로 단계를 다시 계산한다.
"""
import json
import re
from pathlib import Path

# plans.run_input.build_run_input이 만드는 run_id 형식: {run_id}_{task_id}_s{seed}
RUN_ID_PATTERN = re.compile(r"^(?P<run>.+)_(?P<task>[A-Za-z0-9-]+)_s(?P<seed>\d+)$")


class SourceError(ValueError):
    pass


def _read_jsonl(path):
    events = []
    for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise SourceError(f"{path.name} {no}번째 줄이 JSON이 아닙니다.") from exc
    return sorted(events, key=lambda e: e.get("sequence", 0))


def _primary(issues):
    for issue in issues:
        if issue.get("primary"):
            return issue.get("category")
    return None


def load_attempt(attempt_dir):
    """실행 폴더 1개(= RunInput 1건 = 과업 1개 × 시드 1회)를 세션 기록 목록으로 바꾼다."""
    from agent.funnel import funnel_stage

    attempt_dir = Path(attempt_dir)
    input_file, events_file = attempt_dir / "input.json", attempt_dir / "events.jsonl"
    if not input_file.is_file() or not events_file.is_file():
        raise SourceError(f"{attempt_dir}에 input.json 또는 events.jsonl이 없습니다.")
    run_input = json.loads(input_file.read_text(encoding="utf-8"))
    match = RUN_ID_PATTERN.match(run_input.get("run_id", ""))
    if not match:
        raise SourceError(f"run_id 형식이 {{run}}_{{task}}_s{{seed}}가 아닙니다: {run_input.get('run_id')}")
    task_id, seed = match["task"], int(match["seed"])
    success_path, stage_paths = run_input.get("success_path"), run_input.get("stage_paths") or {}

    sessions = {}
    for event in _read_jsonl(events_file):
        sid = event.get("session_id")
        payload = event.get("payload") or {}
        if event.get("type") == "session.started":
            persona_id = (payload.get("persona") or {}).get("id") or sid.removeprefix("session_")
            sessions[sid] = {"task_id": task_id, "seed": seed, "persona_id": persona_id,
                             "status": "incomplete", "reason": "종료 이벤트 없음", "steps": 0,
                             "duration_seconds": None, "stages": [], "causes": [], "primary_cause": None}
        elif sid in sessions and event.get("type") == "observation.created":
            stage = payload.get("stage") or funnel_stage(payload.get("url", "/"), success_path, stage_paths)
            sessions[sid]["stages"].append(stage)
        elif sid in sessions and event.get("type") == "session.finished":
            issues = payload.get("issues") or []
            sessions[sid].update(status=payload.get("status", "incomplete"), reason=payload.get("reason", ""),
                                 steps=payload.get("steps", 0), duration_seconds=payload.get("duration_seconds"),
                                 causes=[i.get("category") for i in issues if i.get("category")],
                                 primary_cause=_primary(issues))
    return list(sessions.values())


def load_from_run_dir(root):
    """root 아래의 실행 폴더(events.jsonl이 있는 폴더)를 모두 읽는다. 순서는 경로순."""
    root = Path(root)
    if not root.is_dir():
        raise SourceError(f"폴더가 없습니다: {root}")
    attempts = sorted({p.parent for p in root.rglob("events.jsonl")})
    if not attempts:
        raise SourceError(f"{root} 아래에 events.jsonl이 없습니다.")
    records = []
    for attempt in attempts:
        records.extend(load_attempt(attempt))
    return records


def load_from_db(run):
    """백엔드 A의 Run → 세션 기록 목록. 아직 끝나지 않은 세션(running)은 incomplete로 센다."""
    from agent.funnel import funnel_stage
    from runs.models import Session

    sessions = (Session.objects.filter(run=run).select_related("persona_snapshot", "task_run")
                .prefetch_related("observations"))
    records = []
    for s in sessions:
        task_input = s.task_run.input_json or {}
        success_path, stage_paths = task_input.get("success_path"), task_input.get("stage_paths") or {}
        stages = [o.stage or funnel_stage(o.url, success_path, stage_paths)
                  for o in sorted(s.observations.all(), key=lambda o: o.step)]
        issues = s.issues or []
        records.append({
            "task_id": s.task_id, "seed": s.seed, "persona_id": s.persona_snapshot.code,
            "status": s.status if s.is_terminal else "incomplete",
            "reason": s.reason, "steps": s.steps, "duration_seconds": s.duration_seconds,
            "stages": stages,
            "causes": [i.get("category") for i in issues if isinstance(i, dict) and i.get("category")],
            "primary_cause": _primary([i for i in issues if isinstance(i, dict)]),
        })
    return records