"""테스트용 합성 실행 폴더를 만든다. 에이전트(agent/runtime.py)가 남기는 파일과 같은 모양이다.

실제 실행 결과가 아니므로 테스트에서만 쓴다.
"""
import json
from pathlib import Path

ORDER_PATHS = ["/", "/product", "/cart", "/signup", "/checkout", "/order/success"]


def write_attempt(root, run_input, outcomes, default=("success", ORDER_PATHS), with_stage=True):
    """run_input: build_run_input 결과.
    outcomes: {persona_id: (status, [경로...]) 또는 (status, [경로...], 주 원인)}. 지정 안 한 페르소나는 default.
    with_stage=False면 예전 에이전트처럼 observation에 stage를 넣지 않는다."""
    from agent.funnel import funnel_stage

    folder = Path(root) / run_input["run_id"] / "attempt"
    folder.mkdir(parents=True)
    (folder / "input.json").write_text(json.dumps(run_input, ensure_ascii=False), encoding="utf-8")
    seq = 0
    lines = []

    def emit(kind, sid, payload):
        nonlocal seq
        seq += 1
        lines.append(json.dumps({"schema_version": "1.0", "run_id": run_input["run_id"], "session_id": sid,
                                 "sequence": seq, "type": kind, "payload": payload}, ensure_ascii=False))

    for persona in run_input["personas"]:
        sid = f"session_{persona['id']}"
        status, paths, *rest = outcomes.get(persona["id"], default)
        cause = rest[0] if rest else None
        emit("session.started", sid, {"persona": persona})
        for i, path in enumerate(paths):
            step_id = f"step_{i:03d}"
            payload = {"step_id": step_id, "url": f"http://shop.local:8080{path}"}
            if with_stage:
                payload.update(stage=funnel_stage(path, run_input["success_path"]),
                               frustration_score=10.0 * i, frustration_threshold=60.0)
            emit("observation.created", sid, payload)
            if cause and status == "simulated_dropout" and i == len(paths) - 1:
                emit("friction.detected", sid, {"category": cause, "evidence_step_ids": [step_id], "contribution": 70.0})
            if i < len(paths) - 1:
                emit("action.completed", sid, {"step_id": step_id,
                                            "action": {"kind": "click", "target_id": f"e{i}", "explanation": "다음으로"}})
        if status != "incomplete":
            issues = [{"category": cause, "primary": status == "simulated_dropout"}] if cause else []
            emit("session.finished", sid, {"status": status, "reason": f"{status} 사유", "steps": len(paths),
                                           "duration_seconds": 2.0 * len(paths), "issues": issues})
    (folder / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return folder