from types import SimpleNamespace
import pytest
from agent.policies import OpenAIPolicy
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
