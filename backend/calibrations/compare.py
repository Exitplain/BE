"""K03 보정 전·후 오차 비교 (CAL-15). 추정하지 않고 실제 실행 결과로만 비교한다 (CAL-10 결정 a).

- 보정 전 = 조정안의 기준 리포트 (basis.report_id)
- 보정 후 = 그 보정 리비전으로 돈 첫 사전 테스트·재테스트 리포트 (원인 재현 제외)
- 둘 다 **지금 시점의 같은 실측 기간**(basis.days, 디바이스 전체)과 비교한다
보정 후 실행이 아직 없으면 status=waiting.
"""
from measurements import services as measure
from reports.models import ReportSnapshot

from .models import CalibrationRevision


def _after_report(project, revision):
    reports = (ReportSnapshot.objects.filter(plan_snapshot__plan__project=project, run__isnull=False)
               .exclude(run__kind="cause_repro").select_related("run").order_by("created_at", "revision"))
    for report in reports:
        if (report.run.config or {}).get("calibration_revision", 0) == revision:
            return report
    return None


def _summary(funnel):
    tasks = [t for t in funnel["tasks"] if t["compared_stages"]]
    errors = [t["mean_error"] for t in tasks if t["mean_error"] is not None]
    stages = [{"task_id": t["task_id"], "task_name": t["name"], "stage_id": s["id"], "stage_name": s["name"],
               "error": s["error"], "gap": s["gap"]}
              for t in tasks for s in t["stages"] if s["compared"]]
    return {"mean_error": round(sum(errors) / len(errors), 1) if errors else None,
            "gaps": sum(1 for s in stages if s["gap"]), "stages": stages}


def compare(project, rev):
    """{revision, status: waiting|ready|unavailable, before, after, stages}"""
    out = {"revision": rev.revision, "status": "unavailable", "before": None, "after": None, "stages": []}
    basis = rev.basis or {}
    site = measure.site_for(project)
    before_report = ReportSnapshot.objects.filter(pk=basis.get("report_id")).first() if basis.get("report_id") else None
    if site is None or before_report is None or rev.restored_from is not None:
        out["message"] = "조정안으로 적용한 보정만 전·후를 비교해요."
        return out
    days = basis.get("days", 7)
    before = _summary(measure.funnel_dict(project, site, days, "all", report=before_report))
    out["before"] = {"report_id": str(before_report.id), **{k: v for k, v in before.items() if k != "stages"}}
    after_report = _after_report(project, rev.revision)
    if after_report is None:
        out.update(status="waiting", message="이 보정으로 돈 사전 테스트가 아직 없어요. 끝나면 같은 실측 기간으로 비교해요.")
        out["stages"] = [{**s, "after": None, "change": None} for s in before["stages"]]
        return out
    after = _summary(measure.funnel_dict(project, site, days, "all", report=after_report))
    out["after"] = {"report_id": str(after_report.id), "run_sequence": after_report.run.sequence_no,
                    **{k: v for k, v in after.items() if k != "stages"}}
    by_key = {(s["task_id"], s["stage_id"]): s for s in after["stages"]}
    rows = []
    for s in before["stages"]:
        a = by_key.get((s["task_id"], s["stage_id"]))
        change = None
        if a and s["error"] is not None and a["error"] is not None:
            if s["gap"] and not a["gap"]:
                change = "차이 구간 해소"
            elif not s["gap"] and a["gap"]:
                change = "차이 구간 생김"
            elif abs(a["error"]) < abs(s["error"]) - 0.5:
                change = "개선"
            elif abs(a["error"]) > abs(s["error"]) + 0.5:
                change = "나빠짐"
            else:
                change = "비슷"
        rows.append({**s, "before": s["error"], "after": a["error"] if a else None, "change": change})
    out.update(status="ready", stages=rows)
    return out


def after_error(project, rev):
    """이력 표의 '보정 후 오차' (없으면 None)."""
    if rev.restored_from is not None or not (rev.basis or {}).get("report_id"):
        return None
    if _after_report(project, rev.revision) is None:
        return None
    return (compare(project, rev)["after"] or {}).get("mean_error")


def revision_or_404(project, revision):
    return CalibrationRevision.objects.filter(project=project, revision=revision).first()
