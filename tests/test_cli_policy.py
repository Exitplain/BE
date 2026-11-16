"""--input으로 실행할 때 고른 정책이 그대로 쓰이는지 (백엔드 워커: AGENT_POLICY)."""
import json
import sys

import pytest

from agent import cli
from agent.policies import ClaudePolicy, OpenAIPolicy, ScriptedPolicy, ShopScriptedPolicy


def run_cli(tmp_path, monkeypatch, *args):
    run_input = {"run_id": "r1", "target_url": "http://127.0.0.1:9/", "allowed_origins": ["http://127.0.0.1:9"],
                 "goal": "주문", "success_path": "/order/success", "personas": [{"id": "P-001"}]}
    path = tmp_path / "input.json"
    path.write_text(json.dumps(run_input), encoding="utf-8")
    used = []

    async def fake_execute_run(config, factory, output_dir):
        used.append(factory)
        out = tmp_path / "out"
        out.mkdir(exist_ok=True)
        return {"output_dir": str(out), "metrics": {}, "sessions": [{"status": "success"}]}

    monkeypatch.setattr(cli, "execute_run", fake_execute_run)
    monkeypatch.setattr(sys, "argv", ["agent.cli", "--input", str(path), "--output", str(tmp_path), *args])
    with pytest.raises(SystemExit) as stop:
        cli.main()
    assert stop.value.code in (0, None)
    summary = json.loads((tmp_path / "out" / "summary.json").read_text(encoding="utf-8"))
    return used, summary


@pytest.mark.parametrize("policy,expected", [("shop_scripted", ShopScriptedPolicy), ("scripted", ScriptedPolicy)])
def test_input_uses_selected_scripted_policy(tmp_path, monkeypatch, policy, expected):
    used, summary = run_cli(tmp_path, monkeypatch, "--policy", policy)
    assert used == [expected]
    assert summary["model_driven"] is False


def test_openai_policy_is_really_used(tmp_path, monkeypatch):
    """--policy openai 가 고정 정책으로 바뀌면서 summary 에는 model_driven=true 로 남던 회귀를 막는다."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-not-a-real-key")
    used, summary = run_cli(tmp_path, monkeypatch, "--policy", "openai", "--model", "test-model")
    assert len(used) == 1 and used[0] not in (ScriptedPolicy, ShopScriptedPolicy)
    policy = used[0]()
    assert isinstance(policy, OpenAIPolicy) and policy.model == "test-model"
    assert (summary["policy"], summary["model"], summary["model_driven"]) == ("openai", "test-model", True)


def test_claude_policy_defaults_to_haiku_with_sonnet_escalation(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only-not-a-real-key")
    used, summary = run_cli(tmp_path, monkeypatch, "--policy", "claude")
    policy = used[0]()
    assert isinstance(policy, ClaudePolicy)
    assert (policy.model, policy.escalation_model) == ("claude-haiku-4-5", "claude-sonnet-5")
    assert (summary["policy"], summary["model"], summary["escalation_model"], summary["model_driven"]) == \
        ("claude", "claude-haiku-4-5", "claude-sonnet-5", True)


def test_claude_policy_requires_credentials_before_launching(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    launched = []
    monkeypatch.setattr(cli, "execute_run", lambda *a, **k: launched.append(a))
    monkeypatch.setattr(sys, "argv", ["agent.cli", "--demo", "normal", "--policy", "claude", "--output", str(tmp_path)])
    with pytest.raises(SystemExit) as stop:
        cli.main()
    assert stop.value.code == 2 and launched == []