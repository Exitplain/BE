from types import SimpleNamespace
import pytest
from agent.policies import ClaudePolicy, OpenAIPolicy
from agent.schemas import Persona, Action


async def test_model_adapter_passes_screenshot_and_validates_action(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-not-a-real-key")
    policy = OpenAIPolicy("test-model")
    requests = []
    async def parse(**kwargs):
        requests.append(kwargs)
        return SimpleNamespace(output_parsed=Action(kind="click", target_id="e0", value=None, explanation="test"),
            usage=SimpleNamespace(input_tokens=23, output_tokens=7))
    monkeypatch.setattr(policy.client.responses, "parse", parse)
    try:
        action, usage = await policy.decide({"elements": [{"id": "e0"}], "screenshot": b"png"},
            Persona(id="A"), "test goal", [])
        assert action.target_id == "e0"
        assert usage == {"input_tokens": 23, "output_tokens": 7}
        assert requests[0]["text_format"] is Action
        assert requests[0]["input"][0]["content"][1]["image_url"].startswith("data:image/png;base64,")
    finally:
        await policy.close()


def test_missing_auth_is_not_silently_replaced(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        OpenAIPolicy("test-model")


# ---------- Claude (Haiku 기본, 막힌 스텝만 Sonnet) — SDK 호출은 가짜로 바꾼다. 실제 API 호출 없음 ----------

def claude_policy(monkeypatch, replies):
    """replies: 모델 호출마다 돌려줄 (action 또는 None, stop_reason) 목록. 호출 기록을 함께 돌려준다."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only-not-a-real-key")
    policy = ClaudePolicy("claude-haiku-4-5", "claude-sonnet-5")   # 에스컬레이션 동작을 보려고 서로 다른 두 모델을 명시한다
    calls = []
    async def parse(**kwargs):
        calls.append(kwargs)
        action, stop_reason = replies[len(calls) - 1]
        return SimpleNamespace(parsed_output=action, stop_reason=stop_reason,
                               usage=SimpleNamespace(input_tokens=100, output_tokens=10))
    monkeypatch.setattr(policy.client.messages, "parse", parse)
    return policy, calls


CLICK = Action(kind="click", target_id="e0", value=None, explanation="담기")
STOP = Action(kind="stop", target_id=None, value=None, explanation="모르겠다")
OBS = {"elements": [{"id": "e0"}], "screenshot": b"png"}


async def test_claude_uses_haiku_with_screenshot_and_reports_usage_by_model(monkeypatch):
    policy, calls = claude_policy(monkeypatch, [(CLICK, "end_turn")])
    try:
        action, usage = await policy.decide(OBS, Persona(id="A"), "goal", [])
    finally:
        await policy.close()
    assert action == CLICK
    assert [c["model"] for c in calls] == ["claude-haiku-4-5"]
    assert calls[0]["output_format"] is Action
    image = calls[0]["messages"][0]["content"][0]
    assert (image["type"], image["source"]["media_type"]) == ("image", "image/png")
    assert usage == {"input_tokens": 100, "output_tokens": 10,
                     "by_model": {"claude-haiku-4-5": {"calls": 1, "input_tokens": 100, "output_tokens": 10}}}


@pytest.mark.parametrize("first", [(STOP, "end_turn"), (None, "refusal"), (CLICK, "max_tokens")])
async def test_claude_asks_sonnet_before_giving_up(monkeypatch, first):
    policy, calls = claude_policy(monkeypatch, [first, (CLICK, "end_turn")])
    try:
        action, usage = await policy.decide(OBS, Persona(id="A"), "goal", [])
    finally:
        await policy.close()
    assert action == CLICK
    assert [c["model"] for c in calls] == ["claude-haiku-4-5", "claude-sonnet-5"]
    assert set(usage["by_model"]) == {"claude-haiku-4-5", "claude-sonnet-5"}
    assert usage["input_tokens"] == 200


async def test_claude_goes_straight_to_sonnet_when_repeating_the_same_action(monkeypatch):
    policy, calls = claude_policy(monkeypatch, [(STOP, "end_turn")])
    # 실제 모델은 같은 버튼을 눌러도 설명 문구를 매번 다르게 쓴다
    history = [{**CLICK.model_dump(), "explanation": "담기"}, {**CLICK.model_dump(), "explanation": "다시 담기"}]
    try:
        action, _ = await policy.decide(OBS, Persona(id="A"), "goal", history)
    finally:
        await policy.close()
    assert action == STOP                       # 상위 모델도 stop 이면 그대로 진행 불가로 끝난다
    assert [c["model"] for c in calls] == ["claude-sonnet-5"]


async def test_claude_keeps_haiku_for_ordinary_repeats(monkeypatch):
    """연속 스크롤이나 다른 버튼 클릭은 헤맴이 아니다 (파일럿에서 스크롤 3번 연속이 정상 탐색이었다)."""
    scroll = {"kind": "scroll", "target_id": None, "value": "down"}
    other = [{**CLICK.model_dump()}, {**CLICK.model_dump(), "target_id": "e1"}]
    for history in ([{**scroll, "explanation": "a"}, {**scroll, "explanation": "b"}], other):
        policy, calls = claude_policy(monkeypatch, [(CLICK, "end_turn")])
        try:
            await policy.decide(OBS, Persona(id="A"), "goal", history)
        finally:
            await policy.close()
        assert [c["model"] for c in calls] == ["claude-haiku-4-5"]


async def test_claude_without_any_valid_action_is_a_failure_not_a_guess(monkeypatch):
    policy, _ = claude_policy(monkeypatch, [(None, "refusal"), (None, "refusal")])
    try:
        with pytest.raises(ValueError, match="no validated action"):
            await policy.decide(OBS, Persona(id="A"), "goal", [])
    finally:
        await policy.close()


def test_claude_missing_auth_is_not_silently_replaced(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        ClaudePolicy()
