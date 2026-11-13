"""Run 과 B의 리포트(reports.ReportSnapshot)가 만나는 유일한 지점.

앱 의존 방향이 reports → runs 라서 여기서 reports 를 import 하지 않고, 호출 시점에 `apps.get_model` 로만 가져온다.
지금 ReportSnapshot 은 Run 을 `run_ref`(문자열 = str(run.id))로 가리킨다. B가 이를 FK 로 바꾸면 이 파일만 고친다.
읽는 값: 리포트 id·상태·revision, data["tasks"][].task_id / name / achievement_rate
"""
from django.apps import apps


def _report_model():
    try:
        return apps.get_model("reports", "ReportSnapshot")
    except LookupError:  # reports 앱이 없는 환경에서도 목록은 동작한다
        return None


def latest_report_summaries(run_ids):
    """{str(run_id): {id, status, revision, goal_rates}}. 리포트가 없는 Run 은 키가 없다. 쿼리 1번."""
    Report = _report_model()
    if Report is None or not run_ids:
        return {}
    latest = {}
    refs = [str(run_id) for run_id in run_ids]
    for report in Report.objects.filter(run_ref__in=refs).order_by("run_ref", "-revision"):
        latest.setdefault(report.run_ref, report)  # 같은 Run 의 첫 행이 가장 높은 revision
    return {ref: _summary(report) for ref, report in latest.items()}


def _summary(report):
    tasks = (report.data or {}).get("tasks") or []
    return {
        "id": str(report.id), "status": report.status, "revision": report.revision,
        "goal_rates": [{"task_id": t.get("task_id"), "name": t.get("name"), "achievement_rate": t.get("achievement_rate")}
                       for t in tasks if isinstance(t, dict)],
    }
