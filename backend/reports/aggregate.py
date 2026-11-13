"""리포트 집계 (Figma P12: KPI, 퍼널 7단계, 시드 범위, 히트맵, 이탈 원인 분포·Top 3, 기술 실패 분리).

DB를 쓰지 않는 순수 함수라 샘플 데이터로 바로 테스트할 수 있다.

규칙
- 세션 분류: success(목표 도달) / dropout(가상 이탈) / technical(기술 실패·한도) / incomplete(결과 없음)
  기술 실패 상태 목록은 agent/persona_report.py의 TECHNICAL을 그대로 쓴다.
- 기술 실패와 결과 없음은 퍼널·달성률·원인 분모에서 뺀다 (API_SPEC 4.3: 기술 실패를 이탈률에 합산하지 않음).
- 퍼널: 과업마다 같은 7단계(agent/funnel.py FUNNEL). 세션이 같은 단계를 여러 번 지나가도 처음 도달만 1번 센다.
  이탈 단계 = 이탈한 세션이 마지막으로 본 화면의 단계.
- 시드 범위: 같은 과업을 시드별로 따로 집계해 최솟값~최댓값을 함께 준다 (예: 완료 17~19명).
  합계 숫자는 모든 시드를 더한 값이다.
- 이탈 원인: 가상 이탈 세션의 주 원인(primary) 분포와 Top 3. 주 원인이 없는 이탈은 'unknown'.
- 예상 세션 수(페르소나 × 과업 × 시드)보다 끝난 세션이 적으면 status = partial.
"""
from collections import Counter, defaultdict
from statistics import median

SCHEMA_VERSION = "report-2"


def technical_statuses():
    from agent.persona_report import TECHNICAL
    return set(TECHNICAL)


def funnel_stages():
    from agent.funnel import FUNNEL
    return FUNNEL


def cause_names():
    from agent.behavior import CAUSES
    return CAUSES


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


def _range(values):
    values = list(values)
    return {"min": min(values), "max": max(values)} if values else {"min": None, "max": None}


def _dedupe(records):
    """같은 (과업, 시드, 페르소나)가 두 번 있으면 결과가 있는 쪽, 그다음 나중 것을 쓴다 (재시도 대비)."""
    chosen = {}
    for r in records:
        key = (r["task_id"], r["seed"], r["persona_id"])
        if key not in chosen or chosen[key]["status"] == "incomplete" or r["status"] != "incomplete":
            chosen[key] = r
    return list(chosen.values())


def _first_reached(stages):
    reached = []
    for stage in stages:
        if stage and stage not in reached:
            reached.append(stage)
    return reached


def _task_report(task, group, personas, seeds, technical, segment_of):
    funnel_keys = [key for key, _ in funnel_stages()]
    names = cause_names()
    kinds = Counter()
    reached = Counter()
    dropout_stage = Counter()
    by_segment = defaultdict(Counter)
    heat = defaultdict(Counter)                      # (세그먼트) → 이탈 단계별 수
    primary = Counter()
    recorded = Counter()
    per_seed = {s: {"success": 0, "dropout": 0, "reached": Counter()} for s in range(1, seeds + 1)}
    durations, steps = [], []
    unknown_stage = 0
    technical_failures = []

    for r in group:
        kind = classify(r["status"], technical)
        kinds[kind] += 1
        segment = segment_of.get(r["persona_id"], ("unknown", "알 수 없음"))
        by_segment[segment][kind] += 1
        if kind == "technical":
            technical_failures.append({"task_id": task["id"], "seed": r["seed"], "persona_id": r["persona_id"],
                                       "status": r["status"], "reason": r["reason"]})
        if kind not in ("success", "dropout"):
            continue  # 퍼널·달성률·원인 분모에서 제외
        stages = _first_reached(r.get("stages") or [])
        unknown_stage += sum(1 for s in stages if s not in funnel_keys)
        stages = [s for s in stages if s in funnel_keys]
        reached.update(stages)
        seed_bucket = per_seed.setdefault(r["seed"], {"success": 0, "dropout": 0, "reached": Counter()})
        seed_bucket[kind] += 1
        seed_bucket["reached"].update(stages)
        if r.get("duration_seconds") is not None:
            durations.append(r["duration_seconds"])
        steps.append(r.get("steps", 0))
        recorded.update(set(r.get("causes") or []))
        if kind == "dropout":
            last = (r.get("stages") or [None])[-1]
            last = last if last in funnel_keys else None
            if last:
                dropout_stage[last] += 1
                heat[segment][last] += 1
            primary[r.get("primary_cause") or "unknown"] += 1

    valid = kinds["success"] + kinds["dropout"]
    seed_values = list(per_seed.values())
    funnel, previous = [], None
    for order, (key, name) in enumerate(funnel_stages()):
        count = reached[key]
        funnel.append({
            "stage_id": f"stage_{key}", "key": key, "name": name, "order": order,
            "reached": count, "reached_range": _range(v["reached"][key] for v in seed_values),
            "rate_of_valid": _rate(count, valid),
            "rate_of_previous": _rate(count, previous) if previous else None,
            "dropouts_here": dropout_stage[key],
        })
        if count:
            previous = count     # 지나가지 않는 단계(예: 가입 없는 과업)는 건너뛰고 앞 단계와 비교

    dropouts = kinds["dropout"]
    causes = [{"key": key, "name": names.get(key, "원인 미상"), "dropouts": primary[key],
               "share": _rate(primary[key], dropouts), "sessions_with_cause": recorded[key]}
              for key in [*names, "unknown"] if primary[key] or recorded[key]]
    causes.sort(key=lambda c: (-c["dropouts"], -c["sessions_with_cause"]))
    expected = len(personas) * seeds
    return {
        "task_id": task["id"], "name": task.get("name", task["id"]),
        "expected_sessions": expected, "recorded_sessions": len(group),
        "success": kinds["success"], "dropout": dropouts, "technical": kinds["technical"],
        "incomplete": kinds["incomplete"] + max(0, expected - len(group)),
        "valid_sessions": valid, "achievement_rate": _rate(kinds["success"], valid),
        "seed_ranges": {
            "success": _range(v["success"] for v in seed_values),
            "dropout": _range(v["dropout"] for v in seed_values),
            "achievement_rate": _range(_rate(v["success"], v["success"] + v["dropout"]) or 0 for v in seed_values),
        },
        "kpis": {
            "median_duration_seconds": round(median(durations), 1) if durations else None,
            "median_steps": median(steps) if steps else None,
        },
        "funnel": funnel,
        "causes": causes,
        "top_causes": [c for c in causes if c["dropouts"]][:3],
        "heatmap": {
            "stages": [key for key, _ in funnel_stages()],
            "rows": [{"segment_id": sid, "name": name, "dropouts": [heat[(sid, name)][k] for k, _ in funnel_stages()]}
                     for (sid, name) in sorted(by_segment)],
        },
        "segments": [{"segment_id": sid, "name": name, "success": c["success"], "dropout": c["dropout"],
                      "technical": c["technical"], "achievement_rate": _rate(c["success"], c["success"] + c["dropout"])}
                     for (sid, name), c in sorted(by_segment.items())],
        "data_quality": {"unknown_stages": unknown_stage},
    }, technical_failures


def build_report_data(snapshot_data, records):
    technical = technical_statuses()
    records = _dedupe(records)
    personas = {p["code"]: p for p in snapshot_data["personas"]}
    segment_of = {code: (p["segment_id"], p["segment_name"]) for code, p in personas.items()}
    seeds = snapshot_data["limits"]["seed_count"]
    tasks_by_id = {t["id"]: t for t in snapshot_data["tasks"]}

    tasks, technical_failures = [], []
    totals = Counter()
    for task in snapshot_data["tasks"]:
        group = [r for r in records if r["task_id"] == task["id"]]
        report, failures = _task_report(task, group, personas, seeds, technical, segment_of)
        tasks.append(report)
        technical_failures.extend(failures)
        totals.update({k: report[k] for k in ("expected_sessions", "recorded_sessions", "success", "dropout",
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
            "목표 도달·가상 이탈은 설정한 성공 경로와 페르소나 수치로 판정한 모의 결과입니다. 실제 사용자 이탈률이 아닙니다.",
            "기술 실패·한도 초과·결과 없음은 퍼널·달성률·원인 분모에서 뺐습니다.",
            "퍼널은 세션마다 단계별 최초 도달만 1번 셉니다. 범위는 시드별 결과의 최솟값~최댓값입니다.",
        ],
    }
