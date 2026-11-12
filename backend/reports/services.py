from django.db import transaction
from django.db.models import Max

from common.exceptions import ApiError

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