"""K01 조정안 (CAL-05~09·19, docs/실측_보정_요구사항.md 2.2·2.4.1). 규칙으로만 만든다 (모델 호출 없음, 같은 입력 → 같은 결과).

근거는 실측(차이 구간) 또는 보정용 사람 관측(가장 불편했던 화면). 10 미만이면 임시값으로만 보여 준다.
strength=gentle 이면 조정 폭을 절반으로 줄인다 ("보정 약하게").

기준은 R08 비교 한 건: 가장 최근 예측 리포트 + 저장한 매핑 + 기간(기본 7일) + 디바이스 전체.
차이 구간마다
- 실측 < 예측 하한 (실제로 더 많이 이탈): 그 단계에서 예측 이탈이 가장 적었던 세그먼트의 인내심 임계치 × (1 − k),
  그 화면 1위 마찰 원인 가중치(전체) × (1 + k)
- 실측 > 예측 상한 (실제로 덜 이탈): 예측 이탈이 가장 많았던 세그먼트의 임계치 × (1 + k), 1위 원인 가중치 × (1 − k)
k = |오차|/100 을 0.05~0.20 으로 자르고 0.05 단위. 실측 숫자 자체는 넣지 않고 배율만 바꾼다.
"""
from agent.behavior import CAUSES
from measurements import services as measure

from . import params as cal
from .services import current

K_RANGE = (0.05, 0.20)
LABEL_THRESHOLD = "인내심 임계치"


def _k(error):
    return cal.snap(min(K_RANGE[1], max(K_RANGE[0], abs(error) / 100)))


def _heat(report, task_id, stage_id):
    """{segment_id: (이름, 그 단계 예측 이탈 수)} — 리포트 실행 퍼널 heatmap."""
    task = next((t for t in report.data.get("tasks") or [] if t.get("task_id") == task_id), {})
    heat = (task.get("journey_funnel") or {}).get("heatmap") or {}
    columns = heat.get("stages") or []
    if stage_id not in columns:
        return {}
    col = columns.index(stage_id)
    return {row["segment_id"]: (row.get("name") or row["segment_id"], (row.get("dropouts") or [0] * len(columns))[col])
            for row in heat.get("rows") or []}


def _top_cause(report, screens, task_id, stage_id):
    """그 화면에서 가장 많은 세션이 겪은 마찰 원인. 없으면 과업 전체 1위."""
    for task in screens.get("tasks") or []:
        if task.get("task_id") != task_id:
            continue
        screen = next((s for s in task.get("screens") or [] if s.get("screen_id") == stage_id), None)
        frictions = sorted((screen or {}).get("frictions") or [], key=lambda f: (-f.get("sessions", 0), f["category"]))
        if frictions:
            return frictions[0]["category"]
        causes = sorted(task.get("causes") or [], key=lambda c: (-c.get("sessions", 0), c["category"]))
        if causes:
            return causes[0]["category"]
    task = next((t for t in report.data.get("tasks") or [] if t.get("task_id") == task_id), {})
    causes = sorted(task.get("causes") or [], key=lambda c: (-c.get("dropouts", 0), c.get("key", "")))
    key = causes[0].get("key") if causes else None
    return key if key in CAUSES else None


def _normalized(params):
    return {sid: v for sid, v in ((params or {}).get("segments") or {}).items() if v}


def _params_of(project, revision):
    """그 리비전의 보정 값 (0 = 기본 모형)."""
    from .models import CalibrationRevision
    if not revision:
        return {}
    rev = CalibrationRevision.objects.filter(project=project, revision=revision).first()
    return _normalized(rev.params if rev else {})


def _raw(params, segment_id, kind, cause=None):
    values = (params.get("segments") or {}).get(segment_id) or {}
    if kind == "threshold":
        return values.get("threshold_scale", 1.0)
    return (values.get("weight_scale") or {}).get(cause, 1.0)


def _bounds(kind):
    return cal.THRESHOLD_RANGE if kind == "threshold" else cal.WEIGHT_RANGE


MIN_EVIDENCE = 10          # 실측 첫 단계 세션 + 보정용 사람 관측 합이 이만큼 모여야 실제 조정안 (그 전에는 임시값)
HUMAN_K = 0.10             # 사람 관측 근거는 실측보다 작게 조정한다
HUMAN_PICK_SHARE = 0.30    # 이 비율 이상이 고른 화면은 "사람에게 불편한 화면"
HUMAN_QUIET_SHARE = 0.10   # 모델 마찰 상위 화면인데 이 비율 미만만 골랐으면 "모델이 과하게 본 화면"
STRENGTHS = {"normal": 1.0, "gentle": 0.5}


class _Collector:
    def __init__(self, strength):
        self.strength = strength
        self.factors, self.reasons = {}, {}

    def bump(self, segment_id, segment_name, kind, cause, k, up, reason):
        k = k * self.strength
        key = (segment_id, kind, cause)
        self.factors[key] = self.factors.get(key, 1.0) * ((1 + k) if up else (1 - k))
        self.reasons.setdefault(key, (segment_name, []))[1].append(reason)

    def items(self, params):
        out = []
        for (segment_id, kind, cause), factor in sorted(self.factors.items(), key=lambda kv: (kv[0][0] != "*", kv[0])):
            now = _raw(params, segment_id, kind, cause)
            low, high = _bounds(kind)
            proposed = cal.snap(min(high, max(low, now * factor)))
            if proposed == cal.snap(now):
                continue
            name, why = self.reasons[(segment_id, kind, cause)]
            out.append({
                "key": f"{segment_id}.{kind}" + (f".{cause}" if cause else ""),
                "segment_id": segment_id, "segment_name": name, "kind": kind, "cause": cause,
                "label": LABEL_THRESHOLD if kind == "threshold" else f"불편한 점 가중치 · {CAUSES[cause]}",
                "current": cal.snap(now), "proposed": proposed, "reason": " · ".join(dict.fromkeys(why)),
            })
        return out


def _from_measurement(report, funnel, collector):
    """실측 차이 구간 → 조정 (CAL-06). 표본이 적은 과업도 넣되, 호출한 쪽이 임시값으로 표시한다."""
    from reports.screens import report_screens
    tasks = [t for t in funnel["tasks"] if t["base_sessions"] and t["compared_stages"]]
    gaps = [(t, st) for t in tasks for st in t["stages"] if st["gap"]]
    screens = report_screens(report) if gaps else {}
    for task, stage in gaps:
        k = _k(stage["error"])
        more = stage["actual"]["rate"] < stage["predicted"]["low"]
        heat = _heat(report, task["task_id"], stage["id"])
        where = f"{task['name']} · {stage['name']}"
        if heat:
            segment_id, (segment_name, _) = (min if more else max)(heat.items(), key=lambda item: (item[1][1], item[0]))
            collector.bump(segment_id, segment_name, "threshold", None, k, not more,
                           f"{where}: 실제 고객 데이터 이탈이 예측보다 {'커요' if more else '작아요'} ({stage['error']:+.1f}%p)")
        cause = _top_cause(report, screens, task["task_id"], stage["id"])
        if cause:
            collector.bump("*", "전체", "weight", cause, k, more,
                           f"{where} 화면에서 가장 많이 겪은 불편한 점이 {CAUSES[cause]}이에요")
    errors = [t["mean_error"] for t in tasks if t["mean_error"] is not None]
    return {
        # 근거 수는 비교 단계가 아직 없어도 첫 단계에 온 실측 세션으로 센다
        "evidence": "measurement", "count": max((t["base_sessions"] for t in funnel["tasks"]), default=0),
        "mean_error": round(sum(errors) / len(errors), 1) if errors else None,
        "gaps": [{"task_id": t["task_id"], "task_name": t["name"], "stage_id": st["id"], "stage_name": st["name"],
                  "error": st["error"], "actual": st["actual"]["rate"], "low": st["predicted"]["low"],
                  "high": st["predicted"]["high"]} for t, st in gaps],
    }


def _from_observations(project, collector):
    """보정용 사람 관측(가장 불편했던 화면·이유) → 조정 (CAL-19). 평가용 관측은 쓰지 않는다 (CAL-18)."""
    from . import observations as obs
    from .models import HumanObservation
    predicted, _ = obs.predictions(project)
    rows = list(HumanObservation.objects.filter(project=project, purpose=HumanObservation.Purpose.CALIBRATION))
    picks, counts = [], {}
    for task_id, task in predicted.items():
        mine = [r for r in rows if r.task_id == task_id]
        if not mine:
            continue
        counts[task_id] = len(mine)
        names = {st["id"]: st["name"] for st in task["stages"]}
        chosen = {}
        for r in mine:
            if r.pain_stage_id:
                chosen.setdefault(r.pain_stage_id, []).append(r.pain_cause)
        for screen, reasons in sorted(chosen.items()):
            share = len(reasons) / len(mine)
            if share < HUMAN_PICK_SHARE or screen in task["pain_top"]:
                continue
            known = [c for c in reasons if c and c != "unknown"]
            if not known:
                continue
            cause = max(sorted(set(known)), key=known.count)
            where = f"{task['name']} · {names.get(screen, screen)}"
            collector.bump("*", "전체", "weight", cause, HUMAN_K, True,
                           f"{where}: 참여자 {len(mine)}명 중 {len(reasons)}명이 가장 불편했다고 골랐는데 모델이 본 불편한 점은 낮아요"
                           f" (이유: {CAUSES[cause]})")
            picks.append({"task_id": task_id, "task_name": task["name"], "stage_id": screen,
                          "stage_name": names.get(screen, screen), "share": round(share * 100, 1)})
        for screen in task["pain_top"]:
            share = len(chosen.get(screen, [])) / len(mine)
            model_causes = task["screen_causes"].get(screen) or []
            if share < HUMAN_QUIET_SHARE and model_causes:
                cause = model_causes[0]
                where = f"{task['name']} · {names.get(screen, screen)}"
                collector.bump("*", "전체", "weight", cause, HUMAN_K, False,
                               f"{where}: 모델은 불편한 점이 크다고 봤는데 참여자 {len(mine)}명 중 거의 고르지 않았어요")
    return {"evidence": "observations", "count": max(counts.values(), default=0), "mean_error": None,
            "gaps": [], "picks": picks}


def build(project, days=7, strength="normal"):
    """{available, provisional, reason?, message?, base_revision, basis, items}

    근거는 실측 세션 + 보정용 사람 관측을 합쳐 센다. 합이 10 미만이면 provisional=True 로 조정안을 보여 주되 적용은 막는다.
    조정은 두 근거의 배율을 곱해 합친다.
    """
    revision, params = current(project)
    collector = _Collector(STRENGTHS.get(strength, 1.0))
    out = {"project_id": project.pk, "base_revision": revision, "days": days, "strength": strength,
           "min_evidence": MIN_EVIDENCE, "provisional": False, "basis": None, "items": [],
           "after_error": None, "after_note": "예측 맞추기 후 오차는 다음 사전 테스트가 끝난 뒤 실제 결과로 보여 줘요."}
    report = measure.latest_report(project)
    if report is None:
        return {**out, "available": False, "reason": "NO_PREDICTION",
                "message": "먼저 사전 테스트를 한 번 실행해야 비교할 수 있어요."}
    used = report.run.config.get("calibration_revision", 0) if report.run_id else 0
    if _params_of(project, used) != _normalized(params):
        return {**out, "available": False, "reason": "WAITING_NEXT_RUN",
                "message": "예측 맞추기를 적용한 뒤 아직 새 사전 테스트가 없어요. 다음 사전 테스트가 끝나면 다시 비교해요."}

    site = measure.site_for(project)
    measured = None
    if site is not None and site.mapping_saved_at:
        funnel = measure.funnel_dict(project, site, days, "all")
        if any(t["base_sessions"] for t in funnel["tasks"]):
            measured = (funnel, _Collector(collector.strength))
    observed = _Collector(collector.strength)
    human = _from_observations(project, observed)

    # 근거는 합쳐서 센다: 실측 세션 + 보정용 사람 관측 (팀 결정: 사람 관측으로 모자란 실측을 채울 수 있다)
    measured_basis = _from_measurement(report, measured[0], measured[1]) if measured else None
    m_count = measured_basis["count"] if measured_basis else 0
    h_count = human["count"]
    total = m_count + h_count
    if total == 0:
        reason = "NOT_CONNECTED" if site is None else "NOT_MAPPED" if not site.mapping_saved_at else "NO_EVIDENCE"
        return {**out, "available": False, "reason": reason,
                "message": "실제 고객 데이터를 연결하거나, 참여자 응답 링크로 사람 관측을 모아 주세요. "
                           f"실제 고객 데이터 방문과 사람 관측을 합쳐 {MIN_EVIDENCE}건부터 실제 조정안을 만들어요."}
    merged = _Collector(collector.strength)
    for part in ([measured[1]] if m_count else []) + ([observed] if h_count else []):
        for key, factor in part.factors.items():
            merged.factors[key] = merged.factors.get(key, 1.0) * factor
            name, why = part.reasons[key]
            merged.reasons.setdefault(key, (name, []))[1].extend(why)
    items = merged.items(params)
    evidence = "mixed" if m_count and h_count else "measurement" if m_count else "observations"
    basis = {
        "evidence": evidence, "count": total, "measurement_count": m_count, "observation_count": h_count,
        "mean_error": measured_basis["mean_error"] if measured_basis else None,
        "gaps": measured_basis["gaps"] if measured_basis else [],
        "picks": human.get("picks", []),
    }
    enough = total >= MIN_EVIDENCE
    out.update(provisional=not enough, items=items, basis={
        "report_id": str(report.id), "run_sequence": report.run.sequence_no if report.run_id else None,
        "days": days, "device": "all", **basis})
    if not enough:
        parts = " + ".join(x for x in (f"실제 고객 데이터 {m_count}회 방문" if m_count else "",
                                       f"사람 관측 {h_count}명" if h_count else "") if x)
        out["message"] = (f"지금은 {parts}(합 {total}건)이라 임시값이에요. "
                          f"합쳐서 {MIN_EVIDENCE}건부터 실제 값으로 바뀌고 적용할 수 있어요.")
    if not items:
        return {**out, "available": True, "reason": "NO_GAP",
                "message": out.get("message") or "모든 단계가 예측과 맞아 바꿀 값이 없어요."}
    unchanged = []
    for persona in report.plan_snapshot.data.get("personas") or []:
        sid = persona.get("segment_id")
        if sid and sid not in {i["segment_id"] for i in items} and sid not in {u["segment_id"] for u in unchanged}:
            unchanged.append({"segment_id": sid, "segment_name": persona.get("segment_name") or sid})
    return {**out, "available": True, "unchanged_segments": unchanged}


def apply_items(params, items):
    """조정안 행들을 현재 값에 반영한 새 params (검사 포함)."""
    segments = {sid: {**values, **({"weight_scale": dict(values["weight_scale"])} if "weight_scale" in values else {})}
                for sid, values in (params.get("segments") or {}).items()}
    for item in items:
        values = segments.setdefault(item["segment_id"], {})
        if item["kind"] == "threshold":
            values["threshold_scale"] = item["proposed"]
        else:
            values.setdefault("weight_scale", {})[item["cause"]] = item["proposed"]
    return cal.validate({"segments": segments})
