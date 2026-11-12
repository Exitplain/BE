"""리포트 집계 (일정표 B: 퍼널(세션별 최초 도달만), 달성률, 기술 실패 분리).

DB를 쓰지 않는 순수 함수라 샘플 데이터로 바로 테스트할 수 있다.

규칙
- 세션 분류: success(목표 도달) / simulated_dropout(가상 이탈) / technical(기술 실패·한도) / incomplete(결과 없음)
  기술 실패 상태 목록은 agent/persona_report.py의 TECHNICAL을 그대로 쓴다.
- 기술 실패와 결과 없음은 퍼널·달성률 분모에서 뺀다 (API_SPEC 4.3: 기술 실패를 이탈률에 합산하지 않음).
- 퍼널: 한 세션이 같은 단계를 여러 번 지나가도 처음 도달한 것만 1번 센다.
  화면 경로 → 단계는 agent/frustration.py STAGES로 바꾸고, 단계 순서·이름은 확정 스냅샷의 과업 퍼널을 따른다.
- 예상 세션 수(페르소나 × 과업 × 시드)보다 끝난 세션이 적으면 status = partial.
"""
from collections import Counter, defaultdict

SCHEMA_VERSION = "report-1"


def technical_statuses():
    from agent.persona_report import TECHNICAL
    return set(TECHNICAL)


def path_to_stage():
    from agent.frustration import STAGES
    return {path: f"stage_{key}" for path, key in STAGES.items()}


def classify(status, technical):
    if status == "success":
        return "success"
    if status == "simulated_dropout":
        return "dropout"
    if status in technical:
        return "technical"
    return "incomplete"


def _rate(numerator, denominator):
    return round(numerator / denominator, 4) if denominator else None


def _dedupe(records):
    """같은 (과업, 시드, 페르소나)가 두 번 있으면 결과가 있는 쪽, 그다음 나중 것을 쓴다 (재시도 대비)."""
    chosen = {}
    for r in records:
        key = (r["task_id"], r["seed"], r["persona_id"])
        if key not in chosen or chosen[key]["status"] == "incomplete" or r["status"] != "incomplete":
            chosen[key] = r
    return list(chosen.values())


def _first_reached(paths, mapping):
    """경로 목록 → 처음 도달한 순서대로 단계 ID (중복 제거). 모르는 경로는 따로 센다."""
    stages, unknown = [], 0
    for path in paths:
        stage = mapping.get(path)
        if stage is None:
            unknown += 1
        elif stage not in stages:
            stages.append(stage)
    return stages, unknown


def build_report_data(snapshot_data, records):
    technical = technical_statuses()
    mapping = path_to_stage()
    records = _dedupe(records)
    personas = {p["code"]: p for p in snapshot_data["personas"]}
    segment_of = {code: (p["segment_id"], p["segment_name"]) for code, p in personas.items()}
    seeds = snapshot_data["limits"]["seed_count"]
    tasks_by_id = {t["id"]: t for t in snapshot_data["tasks"]}

    tasks, technical_failures = [], []
    totals = Counter()
    for task in snapshot_data["tasks"]:
        group = [r for r in records if r["task_id"] == task["id"]]
        expected = len(personas) * seeds
        kinds = Counter()
        reached = Counter()
        dropout_stage = Counter()
        unknown_paths = 0
        by_segment = defaultdict(Counter)

        for r in group:
            kind = classify(r["status"], technical)
            kinds[kind] += 1
            seg_id, seg_name = segment_of.get(r["persona_id"], ("unknown", "알 수 없음"))
            by_segment[(seg_id, seg_name)][kind] += 1
            if kind == "technical":
                technical_failures.append({"task_id": task["id"], "seed": r["seed"], "persona_id": r["persona_id"],
                                           "status": r["status"], "reason": r["reason"]})
            if kind not in ("success", "dropout"):
                continue  # 퍼널·달성률 분모에서 제외
            stages, unknown = _first_reached(r["paths"], mapping)
            unknown_paths += unknown
            reached.update(stages)
            if kind == "dropout" and stages:
                dropout_stage[stages[-1]] += 1

        valid = kinds["success"] + kinds["dropout"]
        funnel, previous = [], None
        for stage in sorted(task["funnel"], key=lambda s: s["order"]):
            count = reached[stage["id"]]
            funnel.append({"stage_id": stage["id"], "name": stage["name"], "order": stage["order"],
                           "reached": count, "rate_of_valid": _rate(count, valid),
                           "rate_of_previous": _rate(count, previous) if previous is not None else None,
                           "dropouts_here": dropout_stage[stage["id"]]})
            previous = count
        known = {s["id"] for s in task["funnel"]}
        off_funnel = sum(c for s, c in reached.items() if s not in known)

        tasks.append({
            "task_id": task["id"], "name": task.get("name", task["id"]),
            "expected_sessions": expected, "recorded_sessions": len(group),
            "success": kinds["success"], "dropout": kinds["dropout"], "technical": kinds["technical"],
            "incomplete": kinds["incomplete"] + max(0, expected - len(group)),
            "valid_sessions": valid, "achievement_rate": _rate(kinds["success"], valid),
            "funnel": funnel,
            "segments": [{"segment_id": sid, "name": name, "success": c["success"], "dropout": c["dropout"],
                          "technical": c["technical"], "achievement_rate": _rate(c["success"], c["success"] + c["dropout"])}
                         for (sid, name), c in sorted(by_segment.items())],
            "data_quality": {"unknown_paths": unknown_paths, "off_funnel_stage_hits": off_funnel},
        })
        totals.update({k: tasks[-1][k] for k in ("expected_sessions", "recorded_sessions", "success", "dropout",
                                                  "technical", "incomplete", "valid_sessions")})

    ignored = sum(1 for r in records if r["task_id"] not in tasks_by_id)  # 확정하지 않은 과업의 기록
    status = "final" if totals["incomplete"] == 0 else "partial"
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "summary": {**dict(totals), "achievement_rate": _rate(totals["success"], totals["valid_sessions"])},
        "tasks": tasks,
        "technical_failures": technical_failures,
        "ignored_records": ignored,
        "persona_generator": snapshot_data.get("persona_generator"),
        "notes": [
            "목표 도달·가상 이탈은 설정한 성공 경로와 좌절 임계치로 판정한 모의 결과입니다. 실제 사용자 이탈률이 아닙니다.",
            "기술 실패·한도 초과·결과 없음은 퍼널과 달성률 분모에서 뺐습니다.",
            "퍼널은 세션마다 단계별 최초 도달만 1번 셉니다.",
        ],
    }