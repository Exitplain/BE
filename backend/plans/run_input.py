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
        "test_accounts (계정 값은 입력에 넣지 않고 워커가 환경변수 DROPSPOT_CREDENTIALS 로만 넘김)",
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


def _target_url(site, task):
    """데모 과업은 ?variant=&case=, 사이트 확인(explore)으로 만든 과업은 사이트 출처 + 과업의 시작 화면.

    시작 화면에도 입력한 주소의 쿼리를 그대로 붙인다. ?variant=problem 처럼 쿼리로 버전을 고르는 사이트는
    쿼리를 버리면 다른 버전(기본값)을 테스트하게 된다.
    """
    if task.get("start_path"):
        parts = urlsplit(site["entry_url"])
        path, _, own_query = task["start_path"].partition("?")
        return urlunsplit((parts.scheme, parts.netloc, path, own_query or parts.query, ""))
    return _entry_url(site["entry_url"], site["variant"], task["case"])


VARIABLE_KEYS = ("patience", "digital_literacy", "price_sensitivity", "trust_need")


def required_field_limit(variables):
    """참고 넘어갈 필수 입력칸 수. 인내심이 높고 신뢰 요구가 낮을수록 많이 참는다 (4~6칸, 가설값).

    예전에는 모든 페르소나가 6칸이라, 지역 조회처럼 4~5칸을 먼저 요구하는 화면에서 아무도 부담을 느끼지 않았다.
    """
    patience = variables.get("patience", 50)
    trust_need = variables.get("trust_need", 50)
    return max(4, min(6, round(3 + 2 * patience / 100 + (100 - trust_need) / 100)))


def _agent_persona(p, frustration_enabled):
    """스냅샷 페르소나 → agent.schemas.Persona. 수치 4개는 에이전트가 이탈 판정에 직접 쓴다 (agent/behavior.py)."""
    variables = p.get("variables") or {}
    persona = {"id": p["code"], "context": p["context"],
               **{k: int(variables[k]) for k in VARIABLE_KEYS if k in variables},
               "max_required_fields": required_field_limit(variables)}
    if not frustration_enabled:
        persona["frustration_threshold"] = NO_DROPOUT_THRESHOLD
    return persona


def build_run_input(snapshot, *, run_id, task_id, seed=1, segment_ids=None):
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
        "target_url": _target_url(site, task),
        "allowed_origins": site["allowed_origins"],
        "goal": task["goal"],
        "success_path": task.get("success_path"),
        "success_criteria": task.get("success_criteria"),
        "personas": [_agent_persona(p, frustration_enabled) for p in _personas(data, segment_ids)],
        "max_steps": limits["max_steps"],
        "timeout_seconds": limits["timeout_seconds"],
        "concurrency": min(limits["concurrency"], 5),
        "seed": seed,
        "viewport_width": width,
        "viewport_height": VIEWPORT_HEIGHT["mobile" if width <= MOBILE_MAX_WIDTH else "desktop"],
        "action_interval_seconds": min(float(limits["request_interval_seconds"]), 5.0),
    }
    accounts = data.get("test_accounts")
    if accounts:
        # 계정 값은 넣지 않는다 (입력 파일로 남음). 워커가 실행할 때 환경변수로만 넘긴다 (runs/worker.py)
        payload.update(login_url=accounts["login_url"] or None, read_only=True)
    try:
        return RunInput.model_validate(payload).model_dump(mode="json")
    except Exception as exc:  # pydantic ValidationError
        raise RunInputError(f"에이전트 입력 규격과 맞지 않습니다: {exc}") from exc


def _personas(data, segment_ids):
    if not segment_ids:
        return data["personas"]
    chosen = [p for p in data["personas"] if p.get("segment_id") in set(segment_ids)]
    if not chosen:
        raise RunInputError("고른 세그먼트에 페르소나가 없습니다.")
    return chosen


def build_run_inputs(snapshot, *, run_id, scope=None):
    """확정된 과업 × 시드의 RunInput 목록. 순서: 과업 → 시드.

    scope(원인 재현): {"task_ids": [...], "segment_ids": [...], "seed_count": n} 로 일부만 돌린다.
    없으면 계획 전체 (사전 테스트·재테스트).
    """
    scope = scope or {}
    tasks = snapshot.data["tasks"]
    if scope.get("task_ids"):
        known = {t["id"] for t in tasks}
        missing = [t for t in scope["task_ids"] if t not in known]
        if missing:
            raise RunInputError(f"확정된 과업이 아닙니다: {', '.join(missing)}")
        tasks = [t for t in tasks if t["id"] in set(scope["task_ids"])]
    seed_count = snapshot.data["limits"]["seed_count"]
    if scope.get("seed_count"):
        seed_count = min(seed_count, scope["seed_count"])
    return [build_run_input(snapshot, run_id=run_id, task_id=t["id"], seed=s, segment_ids=scope.get("segment_ids"))
            for t in tasks for s in range(1, seed_count + 1)]