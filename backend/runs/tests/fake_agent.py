"""테스트용 가짜 에이전트. `python -m agent.cli` 와 같은 인자를 받고, 같은 모양의 파일을 남긴다 (Chromium 불필요).

agent/runtime.py 의 events.jsonl 봉투와 summary.json 구조를 그대로 따른다:
  <output>/<run_id>/<attempt>/{input.json, events.jsonl, summary.json, session_<persona>/step_NNN.png}

환경변수 FAKE_AGENT_MODE
  ok         페르소나마다 3화면을 보고, 짝수 번째는 성공·홀수 번째는 가상 이탈로 끝난다. 종료 코드 0
  technical  마지막 페르소나가 site_failure 로 끝난다. summary 는 남고 종료 코드 1 (실제 agent.cli 와 같다)
  crash      세션을 시작하고 화면 1개를 남긴 뒤 summary 없이 종료 코드 3
  slow       세션을 시작하고 오래 멈춘다 (취소·시간 초과 테스트용)
  nopersona  입력에 없는 페르소나로 session.started 를 보낸다
"""
import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
       b"\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00\x00\x03\x01\x01\x00\x18\xdd\x8d\xb0\x00\x00\x00\x00IEND\xaeB`\x82")
PATHS = ["/", "/product", "/cart"]


class Writer:
    def __init__(self, root, run_id):
        self.attempt = uuid.uuid4().hex
        self.root = Path(root) / run_id / self.attempt
        self.root.mkdir(parents=True)
        self.run_id = run_id
        self.sequence = 0

    def emit(self, kind, session_id, payload):
        self.sequence += 1
        event = {"schema_version": "1.0", "event_id": uuid.uuid4().hex, "run_id": self.run_id,
                 "attempt_id": self.attempt, "session_id": session_id, "sequence": self.sequence,
                 "occurred_at": datetime.now(timezone.utc).isoformat(), "type": kind, "payload": payload}
        with (self.root / "events.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
            f.flush()
        return event

    def screenshot(self, sid, name):
        folder = self.root / sid
        folder.mkdir(exist_ok=True)
        path = folder / name
        path.write_bytes(PNG)
        return str(path)


def observe(writer, sid, step, base_url):
    step_id = f"step_{step:03d}"
    artifact = writer.screenshot(sid, f"{step_id}.png")
    writer.emit("observation.created", sid, {"step_id": step_id, "url": base_url.rstrip("/") + PATHS[step % 3],
                                              "artifact": artifact, "required_fields": 7 if step == 1 else 2,
                                              "text": f"{step}번째 화면"})
    return step_id, artifact


def run(config, output, mode):
    writer = Writer(output, config["run_id"])
    (writer.root / "input.json").write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    personas = config["personas"]
    results = []
    for index, persona in enumerate(personas):
        sid = f"session_{persona['id']}"
        started = time.monotonic()
        started_id = "ghost" if mode == "nopersona" else persona["id"]
        writer.emit("session.started", sid, {"persona": {**persona, "id": started_id}})
        if mode == "slow":
            time.sleep(float(os.environ.get("FAKE_AGENT_SLEEP", "60")))
        if mode == "crash":
            observe(writer, sid, 0, config["target_url"])
            sys.exit(3)
        issues, artifact = [], None
        for step in range(3):
            step_id, artifact = observe(writer, sid, step, config["target_url"])
            if step == 1:
                issue = {"category": "input_burden", "summary": "Visible required fields exceed persona budget",
                         "evidence_step_ids": [step_id], "artifact": artifact, "observed_fields": 7,
                         "configured_limit": 6}
                issues.append(issue)
                writer.emit("friction.detected", sid, {**issue, "frustration_score": 70})
            writer.emit("action.completed", sid, {"step_id": step_id, "action": {
                "kind": "click", "target_id": "btn", "value": None, "explanation": "다음으로 이동"}})
        last = index == len(personas) - 1
        status = "site_failure" if mode == "technical" and last else ("success" if index % 2 == 0 else "simulated_dropout")
        result = {"session_id": sid, "persona_id": persona["id"], "policy": "scripted_fixture", "model_id": None,
                  "status": status, "reason": "fake", "steps": 3, "duration_seconds": round(time.monotonic() - started, 3),
                  "model_calls": 0, "input_tokens": 0, "output_tokens": 0, "issues": issues, "final_artifact": artifact}
        writer.emit("session.finished", sid, result)
        results.append(result)
    summary = {"schema_version": "1.0", "run_id": config["run_id"], "attempt_id": writer.attempt,
               "policy": ["scripted_fixture"], "sessions": results,
               "metrics": {"started": len(results), "success": sum(r["status"] == "success" for r in results),
                           "technical_failure": sum(r["status"] == "agent_failure" for r in results),
                           "model_calls": 0, "cost": None, "cost_completeness": "unavailable"}}
    (writer.root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"policy": "scripted", "runs": [{"output_dir": str(writer.root), "metrics": summary["metrics"]}]}))
    return all(r["status"] in {"success", "simulated_dropout"} for r in results)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", default="artifacts")
    parser.add_argument("--policy", default="scripted")
    parser.add_argument("--model")
    args = parser.parse_args()
    config = json.loads(args.input.read_text(encoding="utf-8"))
    mode = os.environ.get("FAKE_AGENT_MODE", "ok")
    match = os.environ.get("FAKE_AGENT_CRASH_MATCH")  # run_id 에 이 문자열이 있는 실행만 크래시시킨다
    if match and match in config["run_id"]:
        mode = "crash"
    raise SystemExit(0 if run(config, args.output, mode) else 1)


if __name__ == "__main__":
    main()
