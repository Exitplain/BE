"""Claude 모델 이름 환경변수 규칙 (agent/model_env.py). 실제 API 호출은 없다."""
import importlib

import pytest

from agent import model_env
from agent.model_env import DEFAULT_MODEL, model_from_env

ALL = ["DROPSPOT_DEFAULT_MODEL", "DROPSPOT_CLAUDE_MODEL", "DROPSPOT_ESCALATION_MODEL", "DROPSPOT_JUDGE_MODEL",
       "DROPSPOT_EXPLORE_MODEL", "DROPSPOT_CRITERIA_MODEL"]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ALL:
        monkeypatch.delenv(name, raising=False)


def test_default_is_haiku():
    assert DEFAULT_MODEL == "claude-haiku-4-5"
    assert model_from_env("DROPSPOT_JUDGE_MODEL") == DEFAULT_MODEL


def test_specific_variable_beats_global_default(monkeypatch):
    monkeypatch.setenv("DROPSPOT_DEFAULT_MODEL", "model-global")
    assert model_from_env("DROPSPOT_JUDGE_MODEL") == "model-global"
    monkeypatch.setenv("DROPSPOT_JUDGE_MODEL", "model-judge")
    assert model_from_env("DROPSPOT_JUDGE_MODEL") == "model-judge"


def test_empty_values_are_ignored(monkeypatch):
    monkeypatch.setenv("DROPSPOT_JUDGE_MODEL", "   ")
    monkeypatch.setenv("DROPSPOT_DEFAULT_MODEL", "")
    assert model_from_env("DROPSPOT_JUDGE_MODEL") == DEFAULT_MODEL


def test_first_name_wins_in_order(monkeypatch):
    monkeypatch.setenv("DROPSPOT_EXPLORE_MODEL", "model-explore")
    assert model_from_env("DROPSPOT_CRITERIA_MODEL", "DROPSPOT_EXPLORE_MODEL") == "model-explore"
    monkeypatch.setenv("DROPSPOT_CRITERIA_MODEL", "model-criteria")
    assert model_from_env("DROPSPOT_CRITERIA_MODEL", "DROPSPOT_EXPLORE_MODEL") == "model-criteria"


@pytest.mark.parametrize("module, constant, env", [
    ("agent.policies", "CLAUDE_MODEL", "DROPSPOT_CLAUDE_MODEL"),
    ("agent.policies", "CLAUDE_ESCALATION_MODEL", "DROPSPOT_ESCALATION_MODEL"),
    ("agent.judge", "JUDGE_MODEL", "DROPSPOT_JUDGE_MODEL"),
    ("agent.explore", "EXPLORE_MODEL", "DROPSPOT_EXPLORE_MODEL"),
    ("agent.criteria", "CRITERIA_MODEL", "DROPSPOT_CRITERIA_MODEL"),
])
def test_each_module_reads_its_variable_and_defaults_to_haiku(monkeypatch, module, constant, env):
    mod = importlib.import_module(module)
    try:
        importlib.reload(mod)
        assert getattr(mod, constant) == DEFAULT_MODEL
        monkeypatch.setenv(env, "model-from-env")
        importlib.reload(mod)
        assert getattr(mod, constant) == "model-from-env"
        monkeypatch.delenv(env)
        monkeypatch.setenv("DROPSPOT_DEFAULT_MODEL", "model-global")
        importlib.reload(mod)
        assert getattr(mod, constant) == "model-global"
    finally:
        monkeypatch.undo()
        importlib.reload(mod)


def test_criteria_follows_explore_model_when_its_own_is_missing(monkeypatch):
    from agent import criteria
    monkeypatch.setenv("DROPSPOT_EXPLORE_MODEL", "model-explore")
    try:
        importlib.reload(criteria)
        assert criteria.CRITERIA_MODEL == "model-explore"
    finally:
        monkeypatch.undo()
        importlib.reload(criteria)
