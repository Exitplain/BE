import secrets
from datetime import timedelta

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from common.exceptions import ApiError

from . import sessions as session_files
from . import sources
from .aggregate import build_report_data
from .models import ReportSnapshot, ShareLink
from .models import ReportSnapshot


@transaction.atomic
def create_report(plan_snapshot, records, *, run_ref, source, user=None, run=None):
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
        source=source, data=data, created_by=user, run=run,
    )


def create_report_for_run(run, user=None):
    """백엔드 A의 Run(DB)으로 집계. 같은 Run을 다시 집계하면 revision이 올라간다."""
    records = sources.load_from_db(run)
    return create_report(run.plan_snapshot, records, run_ref=str(run.id),
                         source=ReportSnapshot.Source.DATABASE, user=user or run.created_by, run=run)


def finalize_run(run):
    """A의 워커가 실행을 마친 뒤 부르는 함수 (settings.EXITPLAIN_FINALIZE_HOOK = "reports.services:finalize_run")."""
    return create_report_for_run(run)


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
        if report.run_id:   # 실제 실행: A의 DB에서 읽는다
            detail = session_files.load_session_from_db(report.run, task_id, seed, persona_id)
        else:               # 샘플 실행 폴더로 만든 리포트
            attempt = session_files.find_attempt(report.run_ref, task_id, seed, persona_id)
            detail = session_files.load_session(attempt, persona_id)
    except session_files.SessionNotFound as exc:
        raise ApiError(409, "SESSION_FILES_MISSING", f"세션 기록을 찾지 못했습니다: {exc}") from exc
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



# ---------- 공유 링크 (11/17) ----------

SHARE_DAYS_DEFAULT, SHARE_DAYS_MAX = 7, 30


def create_share_link(report, user, days=SHARE_DAYS_DEFAULT):
    return ShareLink.objects.create(report=report, created_by=user, token=secrets.token_urlsafe(24),
                                    expires_at=timezone.now() + timedelta(days=days))


def share_link_dict(link):
    now = timezone.now()
    state = "revoked" if link.revoked_at else ("expired" if link.expires_at <= now else "active")
    return {"id": str(link.id), "report_id": str(link.report_id), "token": link.token,
            "path": f"/api/v1/shared/{link.token}", "state": state,
            "created_at": link.created_at.isoformat(), "expires_at": link.expires_at.isoformat(),
            "revoked_at": link.revoked_at.isoformat() if link.revoked_at else None}


def revoke_share_link(link):
    if link.revoked_at is None:
        link.revoked_at = timezone.now()
        link.save(update_fields=["revoked_at"])
    return link


def shared_report(token):
    """로그인 없이 보는 리포트. 없거나 취소된 링크는 404, 만료된 링크는 410."""
    link = ShareLink.objects.select_related("report__plan_snapshot__plan__project", "report__run").filter(
        token=token).first()
    if link is None or link.revoked_at is not None:
        raise ApiError(404, "NOT_FOUND", "공유 링크를 찾을 수 없습니다.")
    if link.expires_at <= timezone.now():
        raise ApiError(410, "SHARE_LINK_EXPIRED", "공유 기간이 끝난 링크입니다.")
    report = link.report
    project = report.plan_snapshot.plan.project
    body = report_dict(report)
    return {**body, "shared": True, "expires_at": link.expires_at.isoformat(),
            "project": {"name": getattr(project, "name", "")},
            "run": {"name": report.run.name if report.run else report.run_ref}}