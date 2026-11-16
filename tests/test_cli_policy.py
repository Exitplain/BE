"""--input으로 데모 쇼핑몰을 돌릴 때 쓰는 shop_scripted 정책 (백엔드 워커: AGENT_POLICY=shop_scripted)."""
import json
import sys

import pytest

from agent import cli
from agent.policies import ScriptedPolicy, ShopScriptedPolicy


@pytest.mark.parametrize("policy,expected", [("shop_scripted", ShopScriptedPolicy), ("scripted", ScriptedPolicy)])
def test_input_uses_selected_scripted_policy(tmp_path, monkeypatch, policy, expected):
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
    monkeypatch.setattr(sys, "argv", ["agent.cli", "--input", str(path), "--policy", policy, "--output", str(tmp_path)])
    with pytest.raises(SystemExit) as stop:
        cli.main()
    assert stop.value.code in (0, None)
    assert used == [expected]