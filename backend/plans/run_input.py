"""확정 스냅샷 → `python -m agent.cli --input` 용 input.json (백엔드 A의 워커가 호출).

agent.schemas.RunInput 기준으로 만든다 (b/agent-update에서 추가한 칸 포함).
- 과업 1개 × 시드 1회 = RunInput 1건 (RunInput은 goal·success_path가 1개라서)
- 페르소나 수치 4개, 시드, 화면 폭, 요청 간격, 동시 실행을 그대로 넘긴다
- 아래 UNSUPPORTED_FIELDS는 스냅샷에만 있고 에이전트에 넘기지 않는 값이다
반드시 PlanSnapshot.data만 읽는다. 확정 뒤 계획·프로젝트가 바뀌어도 실행 입력은 그대로다.
"""
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.conf import settings

RUN_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")

UNSUPPORTED_FIELDS = (
    "tasks[].funnel (에이전트가 화면 경로로 7단계를 자동 결정)",
    "limits.cost_cap (에이전트에는 금액이 아니라 토큰 상한만 있음)",
)
MOBILE_MAX_WIDTH = 480
VIEWPORT_HEIGHT = {"mobile": 844, "desktop": 800}
NO_DROPOUT_THRESHOLD = 1_000_000   # frustration_enabled=False면 가상 이탈이 일어나지 않게


class RunInputError(ValueError):
    pass


def _entry_url(entry_url, variant, case):
    """데모 쇼핑몰은 ?variant=&case= 로 버전을 고른다 (agent/cli.py --shop과 동일)."""
    if not getattr(settings, "EXITPLAIN_APPEND_DEMO_QUERY", True):
        return entry_url
    parts = urlsplit(entry_url)
    query = dict(parse_qsl(parts.query))
    query.update({"variant": variant, "case": case})
    return urlunsplit(parts._replace(path=parts.path or "/", query=urlencode(query)))


VARIABLE_KEYS = ("patience", "digital_literacy", "price_sensitivity", "trust_need")


def _agent_persona(p, frustration_enabled):
    """스냅샷 페르소나 → agent.schemas.Persona. 수치 4개는 에이전트가 이탈 판정에 직접 쓴다 (agent/behavior.py)."""
    variables = p.get("variables") or {}
    persona = {"id": p["code"], "context": p["context"],
               **{k: int(variables[k]) for k in VARIABLE_KEYS if k in variables}}
    if not frustration_enabled:
        persona["frustration_threshold"] = NO_DROPOUT_THRESHOLD
    return persona


def build_run_input(snapshot, *, run_id, task_id, seed=1):
    """과업 1개·시드 1회의 RunInput dict. agent.schemas.RunInput으로 검증한 뒤 반환한다."""
    from agent.schemas import RunInput

    data = snapshot.data
    if "personas" not in data:
        raise RunInputError("페르소나가 아직 없는 스냅샷입니다.")
    if not isinstance(run_id, str) or not RUN_ID_PATTERN.fullmatch(run_id):
        raise RunInputError("run_id는 영문·숫자·_·- 만 쓸 수 있습니다.")
    task = next((t for t in data["tasks"] if t["id"] == task_id), None)
    if task is None:
        raise RunInputError(f"확정된 과업이 아닙니다: {task_id}")
    limits = data["limits"]
    if not isinstance(seed, int) or isinstance(seed, bool) or not 1 <= seed <= limits["seed_count"]:
        raise RunInputError(f"seed는 1~{limits['seed_count']} 사이여야 합니다.")

    site = data["site"]
    width = limits["viewport_width"]
    frustration_enabled = data.get("frustration_enabled", True)
    payload = {
        "schema_version": "1.0",
        "run_id": f"{run_id}_{task_id}_s{seed}",
        "target_url": _entry_url(site["entry_url"], site["variant"], task["case"]),
        "allowed_origins": site["allowed_origins"],
        "goal": task["goal"],
        "success_path": task["success_path"],
        "personas": [_agent_persona(p, frustration_enabled) for p in data["personas"]],
        "max_steps": limits["max_steps"],
        "timeout_seconds": limits["timeout_seconds"],
        "concurrency": min(limits["concurrency"], 5),
        "seed": seed,
        "viewport_width": width,
        "viewport_height": VIEWPORT_HEIGHT["mobile" if width <= MOBILE_MAX_WIDTH else "desktop"],
        "action_interval_seconds": min(float(limits["request_interval_seconds"]), 5.0),
    }
    try:
        return RunInput.model_validate(payload).model_dump(mode="json")
    except Exception as exc:  # pydantic ValidationError
        raise RunInputError(f"에이전트 입력 규격과 맞지 않습니다: {exc}") from exc


def build_run_inputs(snapshot, *, run_id):
    """확정된 모든 과업 × 시드의 RunInput 목록. 순서: 과업 → 시드."""
    return [build_run_input(snapshot, run_id=run_id, task_id=t["id"], seed=s)
            for t in snapshot.data["tasks"] for s in range(1, snapshot.data["limits"]["seed_count"] + 1)]