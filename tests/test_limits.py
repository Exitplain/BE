import pytest
from pydantic import ValidationError
from agent import RunInput, execute_run
from agent.demo import demo_server
from agent.schemas import Action, Persona


def config(base, **kwargs):
    return RunInput(run_id="limits", target_url=base + "/normal", allowed_origins=[base], goal="모의 주문",
                    success_path="/success", personas=[Persona(id="A")], **kwargs)


def test_concurrency_up_to_five():
    with demo_server() as base:
        assert config(base, concurrency=5).concurrency == 5
        with pytest.raises(ValidationError):
            config(base, concurrency=6)
        with pytest.raises(ValidationError):
            config(base, max_tokens_per_session=0)


async def test_token_budget_stops_session(tmp_path):
    class CostlyPolicy:
        name = "costly"
        async def decide(self, *args):
            return Action(kind="scroll", target_id=None, value="down", explanation="둘러보기"), \
                {"input_tokens": 600, "output_tokens": 100}
    with demo_server() as base:
        limited = await execute_run(config(base, max_tokens_per_session=1000, max_steps=10), CostlyPolicy, tmp_path)
        free = await execute_run(config(base, max_steps=3), CostlyPolicy, tmp_path)
    session = limited["sessions"][0]
    assert (session["status"], session["reason"]) == ("budget_exceeded", "Token budget reached")
    assert session["input_tokens"] + session["output_tokens"] == 1400       # 두 번째 호출에서 1000을 넘어 멈춤
    assert free["sessions"][0]["reason"] != "Token budget reached"