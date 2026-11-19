"""말로 쓴 성공 기준(success_criteria): 정책이 done 을 고르면 별도 판정기가 화면을 보고 확인한다. 실제 API 호출 없음."""
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from agent import RunInput, execute_run
from agent.demo import demo_server
from agent.judge import ClaudeJudge
from agent.policies import ScriptedPolicy
from agent.schemas import Action, Judgment, Persona

DONE = Action(kind="done", target_id=None, value=None, explanation="다 한 것 같아요")


def config(base, **kwargs):
    kwargs.setdefault("success_criteria", "모의 주문이 완료됐다는 화면이 보인다")
    kwargs.setdefault("max_steps", 8)
    return RunInput(run_id="judge", target_url=base + "/normal", allowed_origins=[base], goal="모의 주문",
                    personas=[Persona(id="A")], **kwargs)


class FakeJudge:
    model = "fake-judge"

    def __init__(self, achieved=lambda obs: "/success" in obs["url"]):
        self.achieved, self.calls = achieved, []

    async def judge(self, criteria, goal, observation):
        self.calls.append(observation["url"])
        ok = self.achieved(observation)
        return Judgment(achieved=ok, reason="완료 문구가 보여요" if ok else "아직 주문 화면이에요"), \
            {self.model: {"calls": 1, "input_tokens": 100, "output_tokens": 10}}


class DoneOnSuccessPage(ScriptedPolicy):
    async def decide(self, observation, persona, goal, history):
        if "/success" in observation["url"]:
            return DONE, {}
        return await super().decide(observation, persona, goal, history)


async def test_judge_decides_success_from_the_screen(tmp_path):
    judge, events = FakeJudge(), []
    with demo_server() as base:
        report = await execute_run(config(base), DoneOnSuccessPage, tmp_path, events.append, judge=judge)
    session = report["sessions"][0]
    assert (session["status"], session["reason"]) == ("success", "Success criteria judged achieved on screen")
    assert session["judgments"] == len(judge.calls) >= 1 and "/success" in judge.calls[-1]
    assert session["usage_by_model"]["fake-judge"]["calls"] == session["judgments"]
    judged = [e["payload"] for e in events if e["type"] == "success.judged"]
    assert judged[-1]["achieved"] is True and judged[-1]["reason"] and judged[-1]["trigger"] in {"new_screen", "done"}


async def test_reaching_the_goal_screen_is_enough_without_done(tmp_path):
    """정책이 목표 화면에 와서도 done 을 고르지 않고 계속 진행해도, 새 화면 판정으로 성공을 잡는다."""
    judge, events = FakeJudge(), []
    with demo_server() as base:
        report = await execute_run(config(base), ScriptedPolicy, tmp_path, events.append, judge=judge)
    session = report["sessions"][0]
    assert (session["status"], session["reason"]) == ("success", "Success criteria judged achieved on screen")
    triggers = [e["payload"]["trigger"] for e in events if e["type"] == "success.judged"]
    assert triggers and set(triggers) == {"new_screen"}
    first_url = next(e["payload"]["url"] for e in events if e["type"] == "observation.created")
    assert first_url not in judge.calls                                   # 시작 화면은 판정하지 않는다


async def test_premature_done_is_rejected_and_fed_back(tmp_path):
    seen = []

    class EagerPolicy(DoneOnSuccessPage):
        async def decide(self, observation, persona, goal, history):
            seen.append([h.get("result") for h in history])
            if not history:
                return DONE, {}                      # 첫 화면에서 바로 '다 했다'고 주장
            return await super().decide(observation, persona, goal, history)

    judge = FakeJudge()
    with demo_server() as base:
        report = await execute_run(config(base), EagerPolicy, tmp_path, judge=judge)
    session = report["sessions"][0]
    assert session["status"] == "success" and session["judgments"] >= 2
    assert any(r and r[0] and r[0].startswith("not achieved yet: 아직 주문 화면") for r in seen)


async def test_judgment_limit_caps_cost(tmp_path):
    class AlwaysDone(ScriptedPolicy):
        async def decide(self, *args):
            return DONE, {}

    judge = FakeJudge(achieved=lambda obs: False)
    with demo_server() as base:
        report = await execute_run(config(base, max_judgments_per_session=2), AlwaysDone, tmp_path, judge=judge)
    session = report["sessions"][0]
    assert (session["status"], session["judgments"], len(judge.calls)) == ("budget_exceeded", 2, 2)


async def test_done_without_criteria_is_not_success(tmp_path):
    """성공 경로 방식에서는 done 을 성공으로 치지 않는다 (판정기 호출도 없음)."""
    class AlwaysDone(ScriptedPolicy):
        async def decide(self, *args):
            return DONE, {}

    with demo_server() as base:
        report = await execute_run(config(base, success_criteria=None, success_path="/success", max_steps=3),
                                   AlwaysDone, tmp_path)
    assert report["sessions"][0]["status"] == "budget_exceeded"


async def test_criteria_without_judge_is_refused(tmp_path):
    with demo_server() as base:
        with pytest.raises(ValueError, match="judge"):
            await execute_run(config(base), ScriptedPolicy, tmp_path)


def test_success_path_or_criteria_is_required():
    with pytest.raises(ValidationError, match="success_path or success_criteria"):
        RunInput(run_id="x", target_url="http://a.test/", allowed_origins=["http://a.test"], goal="g",
                 personas=[Persona(id="A")])


async def test_claude_judge_parses_and_fails_closed(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only-not-a-real-key")
    judge = ClaudeJudge()
    replies = [(Judgment(achieved=True, reason="업로드 버튼이 보여요"), "end_turn"), (None, "refusal")]
    calls = []

    async def parse(**kwargs):
        calls.append(kwargs)
        parsed, stop = replies[len(calls) - 1]
        return SimpleNamespace(parsed_output=parsed, stop_reason=stop, usage=SimpleNamespace(input_tokens=50, output_tokens=5))

    monkeypatch.setattr(judge.client.messages, "parse", parse)
    obs = {"url": "https://x.test/a", "title": "t", "text": "본문", "screenshot": b"png"}
    try:
        ok, usage = await judge.judge("업로드 화면", "목표", obs)
        refused, _ = await judge.judge("업로드 화면", "목표", obs)
    finally:
        await judge.close()
    assert ok.achieved is True and usage == {judge.model: {"calls": 1, "input_tokens": 50, "output_tokens": 5}}
    assert refused.achieved is False                                     # 답이 없으면 달성 안 함으로
    assert calls[0]["model"] == judge.model and calls[0]["output_format"] is Judgment
    assert "persona" not in calls[0]["messages"][0]["content"][1]["text"]  # 판정기에는 페르소나를 주지 않는다
