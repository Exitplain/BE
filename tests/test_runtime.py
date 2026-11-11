import asyncio
import json
from pathlib import Path
import pytest
from pydantic import ValidationError
from agent import RunInput, execute_run
from agent.schemas import Persona, Action
from agent.policies import ScriptedPolicy
from agent.demo import demo_server


def config(base, path="normal", **kwargs):
    return RunInput(run_id="test", target_url=base+"/"+path, allowed_origins=[base],
        goal="모의 주문", success_path="/success", personas=[Persona(id="A"), Persona(id="B")], **kwargs)


async def test_real_browser_success_and_isolation(tmp_path):
    events = []
    with demo_server() as base:
        report = await execute_run(config(base), ScriptedPolicy, tmp_path, events.append)
    assert [s["status"] for s in report["sessions"]] == ["success", "success"]
    assert report["metrics"]["model_calls"] == 0
    for session in report["sessions"]:
        folder = Path(report["output_dir"]) / session["session_id"]
        assert Path(session["final_artifact"]).is_file()
        assert (folder / "result.json").is_file()
    observations = [e for e in events if e["type"] == "observation.created" and "/success?" in e["payload"]["url"]]
    initial = [e for e in events if e["type"] == "observation.created" and e["payload"]["step_id"] == "step_000"]
    assert all("initial:null" in e["payload"]["text"] for e in initial)
    assert len(observations) == 2
    for event in observations:
        pid = event["session_id"].removeprefix("session_")
        assert "token="+pid in event["payload"]["url"]
        assert "saved:"+pid+";cookie:participant="+pid in event["payload"]["text"]
    assert len({e["event_id"] for e in events}) == len(events)
    assert [e["sequence"] for e in events] == list(range(1, len(events)+1))


async def test_problem_and_fixed(tmp_path):
    with demo_server() as base:
        problem = await execute_run(config(base, "problem"), ScriptedPolicy, tmp_path)
        fixed = await execute_run(config(base, "fixed"), ScriptedPolicy, tmp_path)
    assert all(s["status"] == "simulated_dropout" and s["issues"] for s in problem["sessions"])
    assert all(s["status"] == "success" and not s["issues"] for s in fixed["sessions"])
    assert problem["attempt_id"] != fixed["attempt_id"]


async def test_invalid_target_is_failure_not_dropout(tmp_path):
    class BadPolicy:
        async def decide(self, *args):
            return Action(kind="click", target_id="nonexistent", value=None, explanation="bad"), {}
    with demo_server() as base:
        report = await execute_run(config(base), BadPolicy, tmp_path)
    assert all(s["status"] == "agent_failure" for s in report["sessions"])


async def test_cancellation_and_time_budget(tmp_path):
    class SlowPolicy:
        async def decide(self, *args):
            await asyncio.sleep(5)
    with demo_server() as base:
        cancelled = await execute_run(config(base), ScriptedPolicy, tmp_path, should_cancel=lambda: True)
        timeout = await execute_run(config(base, timeout_seconds=0.1), SlowPolicy, tmp_path)
    assert all(s["status"] == "cancelled" for s in cancelled["sessions"])
    assert all(s["status"] == "budget_exceeded" for s in timeout["sessions"])


async def test_last_action_can_succeed(tmp_path):
    with demo_server() as base:
        report = await execute_run(config(base, max_steps=2), ScriptedPolicy, tmp_path)
    assert all(s["status"] == "success" for s in report["sessions"])


def test_rejects_path_traversal_and_duplicate_ids():
    with demo_server() as base:
        raw = config(base).model_dump()
    raw["run_id"] = "../../escape"
    with pytest.raises(ValidationError):
        RunInput.model_validate(raw)
    raw["run_id"] = "safe"
    raw["personas"][1]["id"] = "A"
    with pytest.raises(ValidationError):
        RunInput.model_validate(raw)
