from django.db import transaction
from django.db.models import Max

from common.exceptions import ApiError

from . import sessions as session_files
from . import sources
from .aggregate import build_report_data
from .models import ReportSnapshot


@transaction.atomic
def create_report(plan_snapshot, records, *, run_ref, source, user=None):
    """세션 기록으로 리포트를 집계해 저장한다. 같은 run_ref를 다시 집계하면 revision이 1 올라간 새 행이 생긴다."""
    if not run_ref or len(run_ref) > 100:
        raise ApiError(422, "VALIDATION_ERROR", "run_ref는 1~100자여야 합니다.", {"run_ref": ["길이를 확인해주세요."]})
    if "personas" not in plan_snapshot.data:
        raise ApiError(409, "PERSONAS_MISSING", "페르소나가 없는 스냅샷이라 집계할 수 없습니다.")
    data = build_report_data(plan_snapshot.data, records)
    data["plan_revision_id"] = str(plan_snapshot.id)
    data["run_ref"] = run_ref
    last = (ReportSnapshot.objects.select_for_update().filter(run_ref=run_ref)
            .aggregate(m=Max("revision"))["m"] or 0)
    return ReportSnapshot.objects.create(
        plan_snapshot=plan_snapshot, run_ref=run_ref, revision=last + 1, status=data["status"],
        source=source, data=data, created_by=user,
    )


def create_report_from_run_dir(plan_snapshot, run_dir, *, run_ref, user=None):
    """샘플 실행 폴더(events.jsonl)로 집계. 백엔드 A의 워커가 생기기 전 확인용."""
    try:
        records = sources.load_from_run_dir(run_dir)
    except sources.SourceError as exc:
        raise ApiError(422, "INVALID_RUN_FILES", str(exc)) from exc
    return create_report(plan_snapshot, records, run_ref=run_ref,
                         source=ReportSnapshot.Source.SAMPLE_FILES, user=user)


def latest_for_run(run_ref):
    return ReportSnapshot.objects.select_related("plan_snapshot__plan__project").filter(run_ref=run_ref).order_by(
        "-revision").first()


def report_dict(report):
    return {"report_id": str(report.id), "run_ref": report.run_ref, "revision": report.revision,
            "status": report.status, "source": report.source, "kind": "prediction",
            "plan_revision_id": str(report.plan_snapshot_id), "created_at": report.created_at.isoformat(),
            "data": report.data}


# ---------- 세션 목록·상세 (11/14) ----------

SESSION_FILTERS = ("task_id", "seed", "status", "kind", "segment_id")


def session_rows(report, filters):
    rows = report.data.get("sessions", [])
    for key in SESSION_FILTERS:
        value = filters.get(key)
        if value not in (None, ""):
            rows = [r for r in rows if str(r.get(key)) == str(value)]
    return rows


def session_detail(report, task_id, seed, persona_id):
    row = next((r for r in report.data.get("sessions", []) if r["task_id"] == task_id and r["seed"] == seed
                and r["persona_id"] == persona_id), None)
    if row is None:
        raise ApiError(404, "NOT_FOUND", "이 리포트에 없는 세션입니다.")
    persona = next((p for p in report.plan_snapshot.data.get("personas", []) if p["code"] == persona_id), {})
    try:
        attempt = session_files.find_attempt(report.run_ref, task_id, seed, persona_id)
        detail = session_files.load_session(attempt, persona_id)
    except session_files.SessionNotFound as exc:
        raise ApiError(409, "SESSION_FILES_MISSING", f"세션 기록 파일을 찾지 못했습니다: {exc}") from exc
    task = next((t for t in report.plan_snapshot.data.get("tasks", []) if t["id"] == task_id), {})
    return {"report_id": str(report.id), "run_ref": report.run_ref, "task": {"id": task_id, "name": task.get("name")},
            "seed": seed,
            "persona": {"code": persona_id, "segment_id": persona.get("segment_id"),
                        "segment_name": persona.get("segment_name"), "variables": persona.get("variables"),
                        "context": persona.get("context")},
            **detail}


# ---------- 진단 기록 목록·대시보드 (11/15) ----------

def _top_causes(report, limit=3):
    totals = {}
    for task in report.data.get("tasks", []):
        for cause in task.get("causes", []):
            entry = totals.setdefault(cause["key"], {"key": cause["key"], "name": cause["name"], "dropouts": 0})
            entry["dropouts"] += cause["dropouts"]
    ranked = sorted((c for c in totals.values() if c["dropouts"]), key=lambda c: -c["dropouts"])
    return ranked[:limit]


def report_summary(report):
    summary = report.data.get("summary", {})
    return {"report_id": str(report.id), "run_ref": report.run_ref, "revision": report.revision,
            "status": report.status, "created_at": report.created_at.isoformat(),
            "plan_revision_id": str(report.plan_snapshot_id),
            "tasks": [{"id": t["task_id"], "name": t["name"], "achievement_rate": t["achievement_rate"]}
                      for t in report.data.get("tasks", [])],
            "achievement_rate": summary.get("achievement_rate"),
            "success": summary.get("success", 0), "dropout": summary.get("dropout", 0),
            "technical": summary.get("technical", 0), "incomplete": summary.get("incomplete", 0),
            "top_causes": _top_causes(report)}


def project_reports(project):
    """실행(run_ref)마다 가장 최근 revision 1개씩, 최신순."""
    latest = {}
    for report in (ReportSnapshot.objects.select_related("plan_snapshot")
                   .filter(plan_snapshot__plan__project=project).order_by("-created_at", "-revision")):
        latest.setdefault(report.run_ref, report)
    return list(latest.values())


def dashboard(project, trend_size=10):
    reports = project_reports(project)
    latest = reports[0] if reports else None
    trend = [{"run_ref": r.run_ref, "created_at": r.created_at.isoformat(),
              "achievement_rate": r.data.get("summary", {}).get("achievement_rate")}
             for r in reversed(reports[:trend_size])]          # 오래된 것 → 최신
    return {"project_id": project.pk, "runs": len(reports),
            "latest": report_summary(latest) if latest else None,
            "trend": trend,
            "recent": [report_summary(r) for r in reports[:5]]}