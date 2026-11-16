import pytest
from pydantic import ValidationError
from agent.schemas import Persona, RunInput


def run_input(**kwargs):
    return RunInput(run_id="r", target_url="http://127.0.0.1:8000/", allowed_origins=["http://127.0.0.1:8000"],
                    goal="주문", success_path="/done", personas=[Persona(id="a")], **kwargs)


def test_defaults_keep_old_inputs_valid():
    config = run_input()
    persona = config.personas[0]
    assert (persona.patience, persona.digital_literacy, persona.price_sensitivity, persona.trust_need) == (50, 50, 50, 50)
    assert (config.seed, config.viewport_width, config.viewport_height, config.action_interval_seconds) == (0, 1000, 800, 0)


def test_new_fields_accepted():
    persona = Persona(id="a", patience=20, digital_literacy=80, price_sensitivity=90, trust_need=10)
    config = run_input(seed=3, viewport_width=390, viewport_height=844, action_interval_seconds=1)
    assert persona.price_sensitivity == 90 and config.viewport_width == 390 and config.seed == 3


@pytest.mark.parametrize("field", ["patience", "digital_literacy", "price_sensitivity", "trust_need"])
def test_persona_values_must_be_0_to_100(field):
    with pytest.raises(ValidationError):
        Persona(id="a", **{field: 101})
    with pytest.raises(ValidationError):
        Persona(id="a", **{field: -1})


def test_run_limits():
    for bad in [dict(seed=-1), dict(viewport_width=100), dict(action_interval_seconds=10)]:
        with pytest.raises(ValidationError):
            run_input(**bad)