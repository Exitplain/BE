"""페르소나 수치·시드가 이탈 판정에 반영되는지 (브라우저 없이) + 실제 브라우저 실행에서 연결 확인."""
import json
from pathlib import Path
from agent import RunInput, execute_run
from agent.behavior import PersonaMeter
from agent.demo import demo_server
from agent.policies import ScriptedPolicy
from agent.schemas import Persona


def drop_step(persona, seed=0, pages=(("landing", 8),)):
    meter = PersonaMeter(persona, seed)
    for i, (stage, fields) in enumerate(pages):
        if meter.observe(stage, fields)["crossed"]:
            return i
    return None


def test_default_matches_old_rule():
    meter = PersonaMeter(Persona(id="a"))
    assert meter.threshold == 60
    point = meter.observe("landing", 7)          # 6개 초과 → 70 − 4(새 단계) = 66 ≥ 60
    assert point["crossed"] and point["signals"] == {"input_burden": 70.0}
    assert not PersonaMeter(Persona(id="a")).observe("landing", 6)["crossed"]


def test_patience_and_trust_change_outcome():
    assert drop_step(Persona(id="a", patience=100)) is None          # 임계치 90 > 66
    assert drop_step(Persona(id="a", patience=100, trust_need=100)) == 0   # 입력 부담 105 − 4
    assert drop_step(Persona(id="a", trust_need=0)) is None            # 입력 부담 35 − 4


def test_late_cost_only_counts_when_not_shown_early():
    sensitive = Persona(id="a", price_sensitivity=100, patience=0)    # 비용 60, 임계치 30
    late = PersonaMeter(sensitive)
    late.observe("detail", 0, "반찬 식탁")
    point = late.observe("payment", 0, "배송비 3,500원이 추가됩니다.")
    assert point["signals"] == {"cost": 60.0} and point["crossed"] and late.primary_cause() == "cost"
    early = PersonaMeter(sensitive)
    early.observe("detail", 0, "배송비 회당 3,500원")
    assert early.observe("payment", 0, "배송비 3,500원이 추가됩니다.")["signals"] == {}


def test_low_digital_literacy_frustrated_by_wandering():
    def wander(literacy):
        meter = PersonaMeter(Persona(id="a", digital_literacy=literacy))
        meter.observe("browse", 0)
        return [meter.observe("browse", 0, last_action="scroll")["score"] for _ in range(8)][-1]
    assert wander(0) > wander(100)
    meter = PersonaMeter(Persona(id="a", digital_literacy=0, patience=0))
    meter.observe("browse", 0)
    points = [meter.observe("browse", 0, last_action="scroll") for _ in range(10)]
    assert any(p["crossed"] for p in points) and meter.primary_cause() == "navigation"


def test_seed_zero_is_fixed_and_seeds_vary():
    persona = Persona(id="p7")
    assert PersonaMeter(persona, 0).threshold == 60
    assert PersonaMeter(persona, 3).threshold == PersonaMeter(persona, 3).threshold      # 같은 시드 → 같은 값
    thresholds = {PersonaMeter(persona, s).threshold for s in range(1, 6)}
    assert len(thresholds) > 1 and all(51 <= t <= 69 for t in thresholds)
    # 경계에 있는 페르소나는 시드에 따라 이탈 여부가 갈린다
    edge = Persona(id="edge", frustration_threshold=66)
    outcomes = {drop_step(edge, seed) for seed in range(1, 20)}
    assert outcomes == {0, None}


def config(base, path, **kwargs):
    return RunInput(run_id="behavior", target_url=base + "/" + path, allowed_origins=[base],
                    goal="모의 주문", success_path="/success", **kwargs)


async def test_runtime_uses_persona_values_viewport_and_stage(tmp_path):
    events = []
    personas = [Persona(id="calm", patience=100), Persona(id="default")]
    with demo_server() as base:
        report = await execute_run(config(base, "problem", personas=personas, viewport_width=390, viewport_height=844,
                                          seed=0), ScriptedPolicy, tmp_path, events.append)
    status = {s["persona_id"]: s for s in report["sessions"]}
    assert status["default"]["status"] == "simulated_dropout"
    assert status["default"]["issues"][0]["primary"] is True
    assert status["calm"]["status"] != "simulated_dropout"
    observed = [e for e in events if e["type"] == "observation.created"]
    assert all("stage" in e["payload"] and "frustration_score" in e["payload"] for e in observed)
    assert status["default"]["terminal_stage"] == status["default"]["stages_reached"][-1]
    step = next(Path(report["output_dir"]).glob("session_default/step_000.json"))
    assert json.loads(step.read_text(encoding="utf-8"))["viewport"]["width"] == 390   # 실제 브라우저 화면 폭