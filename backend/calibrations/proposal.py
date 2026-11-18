"""K01 조정안 (CAL-05~09, docs/실측_보정_요구사항.md 2.2). 규칙으로만 만든다 (모델 호출 없음, 같은 입력 → 같은 결과).

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


def build(project, days=7):
    """{available, reason?, message?, base_revision, basis, items}"""
    revision, params = current(project)
    out = {"project_id": project.pk, "base_revision": revision, "days": days, "basis": None, "items": [],
           "after_error": None, "after_note": "보정 후 오차는 다음 사전 테스트가 끝난 뒤 실제 결과로 보여 줘요."}
    site = measure.site_for(project)
    if site is None:
        return {**out, "available": False, "reason": "NOT_CONNECTED", "message": "실측 데이터를 먼저 연결해 주세요."}
    if not site.mapping_saved_at:
        return {**out, "available": False, "reason": "NOT_MAPPED", "message": "단계 매핑을 먼저 저장해 주세요."}
    report = measure.latest_report(project)
    if report is None:
        return {**out, "available": False, "reason": "NO_PREDICTION",
                "message": "먼저 사전 테스트를 한 번 실행해야 비교할 수 있어요."}
    used = report.run.config.get("calibration_revision", 0) if report.run_id else 0
    if _params_of(project, used) != _normalized(params):
        return {**out, "available": False, "reason": "WAITING_NEXT_RUN", "base_revision": revision,
                "message": "보정을 적용한 뒤 아직 새 사전 테스트가 없어요. 다음 사전 테스트가 끝나면 다시 비교해요."}
    funnel = measure.funnel_dict(project, site, days, "all")
    comparable = [t for t in funnel["tasks"] if not t["collecting"] and t["compared_stages"]]
    errors = [t["mean_error"] for t in comparable if t["mean_error"] is not None]
    gaps = [(t, s) for t in comparable for s in t["stages"] if s["gap"]]
    out["basis"] = {
        "report_id": str(report.id), "run_sequence": report.run.sequence_no if report.run_id else None,
        "days": days, "device": "all",
        "mean_error": round(sum(errors) / len(errors), 1) if errors else None,
        "gaps": [{"task_id": t["task_id"], "task_name": t["name"], "stage_id": s["id"], "stage_name": s["name"],
                  "error": s["error"], "actual": s["actual"]["rate"], "low": s["predicted"]["low"],
                  "high": s["predicted"]["high"]} for t, s in gaps],
    }
    if not comparable:
        return {**out, "available": False, "reason": "INSUFFICIENT_SAMPLE",
                "message": f"실측 세션이 단계마다 {measure.MIN_SAMPLE}개는 모여야 조정안을 만들 수 있어요."}
    if not gaps:
        return {**out, "available": True, "reason": "NO_GAP", "message": "모든 단계가 예측 범위 안이라 바꿀 값이 없어요."}

    from reports.screens import report_screens
    screens = report_screens(report)
    factors, reasons = {}, {}

    def bump(segment_id, segment_name, kind, cause, factor, reason):
        key = (segment_id, kind, cause)
        factors[key] = factors.get(key, 1.0) * factor
        reasons.setdefault(key, (segment_name, []))[1].append(reason)

    for task, stage in gaps:
        k = _k(stage["error"])
        more = stage["actual"]["rate"] < stage["predicted"]["low"]
        heat = _heat(report, task["task_id"], stage["id"])
        where = f"{task['name']} · {stage['name']}"
        if heat:
            pick = (min if more else max)(heat.items(), key=lambda item: (item[1][1], item[0]))
            segment_id, (segment_name, _) = pick
            if more:
                bump(segment_id, segment_name, "threshold", None, 1 - k,
                     f"{where}: 실측 이탈이 예측보다 커요 ({stage['error']:+.1f}%p)")
            else:
                bump(segment_id, segment_name, "threshold", None, 1 + k,
                     f"{where}: 실측 이탈이 예측보다 작아요 ({stage['error']:+.1f}%p)")
        cause = _top_cause(report, screens, task["task_id"], stage["id"])
        if cause:
            bump("*", "전체", "weight", cause, 1 + k if more else 1 - k,
                 f"{where} 화면에서 가장 많이 겪은 마찰이 {CAUSES[cause]}이에요")

    items = []
    for (segment_id, kind, cause), factor in sorted(factors.items(), key=lambda kv: (kv[0][0] != "*", kv[0])):
        now = _raw(params, segment_id, kind, cause)
        low, high = _bounds(kind)
        proposed = cal.snap(min(high, max(low, now * factor)))
        if proposed == cal.snap(now):
            continue
        name, why = reasons[(segment_id, kind, cause)]
        items.append({
            "key": f"{segment_id}.{kind}" + (f".{cause}" if cause else ""),
            "segment_id": segment_id, "segment_name": name, "kind": kind, "cause": cause,
            "label": LABEL_THRESHOLD if kind == "threshold" else f"마찰 가중치 · {CAUSES[cause]}",
            "current": cal.snap(now), "proposed": proposed, "reason": " · ".join(dict.fromkeys(why)),
        })
    unchanged = []
    for persona in report.plan_snapshot.data.get("personas") or []:
        sid = persona.get("segment_id")
        if sid and sid not in {i["segment_id"] for i in items} and sid not in {u["segment_id"] for u in unchanged}:
            unchanged.append({"segment_id": sid, "segment_name": persona.get("segment_name") or sid})
    return {**out, "available": True, "items": items, "unchanged_segments": unchanged}


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
