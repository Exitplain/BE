"""실행(Run) API의 업무 규칙: 실행 목록(P01·C08), 실행 생성(P07~P10), 취소(RUN-06)."""
import uuid
from datetime import timedelta
from urllib.parse import urlsplit

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Max, Q
from django.utils import timezone

from common.exceptions import ApiError
from plans.run_input import RunInputError, build_run_inputs
from projects.models import Project

from . import cursors, netguard, report_link
from .models import Run, TaskRun

PERIOD_DAYS = {"7d": 7, "30d": 30, "90d": 90}
RETEST_BASE_STATUSES = (Run.Status.COMPLETED, Run.Status.PARTIALLY_COMPLETED)
SEQUENCE_RETRIES = 3


def _encode_cursor(run):
    return cursors.encode(run.sequence_no)


def _decode_cursor(cursor):
    return cursors.decode(cursor)[0]


def _iso(value):
    return value.isoformat() if value else None


def run_item(run, report=None):
    return {
        "id": str(run.id), "sequence_no": run.sequence_no, "name": run.name or f"{run.sequence_no}차",
        "kind": run.kind, "status": run.status, "change_note": run.change_note,
        "parent_run_id": str(run.parent_run_id) if run.parent_run_id else None,
        "created_at": _iso(run.created_at), "started_at": _iso(run.started_at), "finished_at": _iso(run.finished_at),
        "report": report,
    }


def list_runs(project, *, q="", statuses=(), kinds=(), period="all", limit=20, cursor=""):
    """프로젝트의 실행을 번호 최신순으로. 필터: 상태·유형·기간·검색어(이름 또는 실행 ID)."""
    queryset = Run.objects.filter(project=project)
    if statuses:
        queryset = queryset.filter(status__in=statuses)
    if kinds:
        queryset = queryset.filter(kind__in=kinds)
    if period in PERIOD_DAYS:
        queryset = queryset.filter(created_at__gte=timezone.now() - timedelta(days=PERIOD_DAYS[period]))
    text = q.strip()
    if text:
        queryset = queryset.filter(Q(name__icontains=text) | Q(id__icontains=text))
    total = queryset.count()
    if cursor:
        queryset = queryset.filter(sequence_no__lt=_decode_cursor(cursor))
    rows = list(queryset.order_by("-sequence_no")[: limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]
    reports = report_link.latest_report_summaries([run.id for run in rows])
    return {"items": [run_item(run, reports.get(str(run.id))) for run in rows],
            "next_cursor": _encode_cursor(rows[-1]) if has_more else None, "total": total}


# ---------- 실행 생성 (B → A 인터페이스: runs.services.create_run) ----------

def _origin(url):
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _validate_request(project, plan_snapshot, kind, parent_run):
    if plan_snapshot.plan.project_id != project.pk:
        raise ApiError(422, "PLAN_PROJECT_MISMATCH", "이 프로젝트의 계획이 아닙니다.",
                       {"plan_revision_id": ["이 프로젝트의 계획이 아닙니다."]})
    if kind == Run.Kind.RETEST:
        if parent_run is None or parent_run.project_id != project.pk or parent_run.status not in RETEST_BASE_STATUSES:
            raise ApiError(422, "INVALID_BASE_RUN", "완료된 이전 실행을 기준으로만 재테스트할 수 있어요.",
                           {"base_run_id": ["완료된 이전 실행을 선택해주세요."]})
    elif parent_run is not None:
        raise ApiError(422, "INVALID_BASE_RUN", "재테스트에서만 이전 실행을 지정할 수 있어요.",
                       {"base_run_id": ["재테스트에서만 지정할 수 있어요."]})


def _existing_by_key(project, key, plan_snapshot):
    run = Run.objects.filter(project=project, idempotency_key=key).first()
    if run is not None and run.plan_snapshot_id != plan_snapshot.pk:
        raise ApiError(409, "IDEMPOTENCY_KEY_REUSED", "같은 Idempotency-Key로 다른 요청을 보낼 수 없어요.")
    return run


def _check_targets(inputs):
    origins = {_origin(i["target_url"]) for i in inputs} | {o for i in inputs for o in i["allowed_origins"]}
    for origin in sorted(origins):
        try:
            netguard.check_origin(origin)
        except netguard.TargetNotAllowed as exc:
            raise ApiError(422, "TARGET_NOT_ALLOWED", str(exc), {"entry_url": [str(exc)]}) from None


def _split_agent_run_id(agent_run_id, run_id):
    """"{run_id}_{task_id}_s{seed}" → (task_id, seed). 형식은 plans.run_input.build_run_input 이 만든다."""
    task_id, _, seed = agent_run_id[len(run_id) + 1:].rpartition("_s")
    return task_id, int(seed)


def _insert_run(project, plan_snapshot, created_by, run_id, inputs, *, kind, parent_run, change_note, key):
    with transaction.atomic():
        locked = Project.objects.select_for_update().get(pk=project.pk)  # 번호 채번과 동시 실행 제한을 직렬화한다
        limit = settings.EXITPLAIN_MAX_ACTIVE_RUNS_PER_PROJECT
        if limit and Run.objects.filter(project=locked, status__in=Run.ACTIVE_STATUSES).count() >= limit:
            raise ApiError(409, "RUN_IN_PROGRESS", "이미 진행 중인 실행이 있어요. 끝난 뒤에 다시 시작해주세요.")
        sequence_no = (Run.objects.filter(project=locked).aggregate(m=Max("sequence_no"))["m"] or 0) + 1
        run = Run.objects.create(
            id=run_id, project=locked, created_by=created_by, sequence_no=sequence_no, kind=kind, parent_run=parent_run,
            change_note=change_note, plan_snapshot=plan_snapshot, idempotency_key=key,
            config={"policy": settings.AGENT_POLICY, "model": settings.AGENT_MODEL, "task_count": len(inputs),
                    "expected_sessions": sum(len(i["personas"]) for i in inputs)})
        task_runs = []
        for agent_input in inputs:
            task_id, seed = _split_agent_run_id(agent_input["run_id"], str(run_id))
            task_runs.append(TaskRun(run=run, task_key=task_id, seed=seed, input_json=agent_input,
                                     agent_run_id=agent_input["run_id"]))
        TaskRun.objects.bulk_create(task_runs)
        Project.objects.filter(pk=locked.pk).update(updated_at=timezone.now())  # 프로젝트 목록의 '수정일'
        return run


def create_run(project, plan_snapshot, created_by, *, idempotency_key=None, kind=Run.Kind.PRETEST, parent_run=None,
               change_note=""):
    """확정된 계획으로 실행을 만든다. (run, created) 를 돌려준다.

    같은 Idempotency-Key 로 다시 부르면 새로 만들지 않고 기존 실행을 돌려준다 (created=False).
    번호 채번은 프로젝트 행을 잠가 직렬화하고, 그래도 겹치면 몇 번 다시 시도한다.
    """
    _validate_request(project, plan_snapshot, kind, parent_run)
    key = (idempotency_key or "").strip()[:100] or None
    if key and (existing := _existing_by_key(project, key, plan_snapshot)):
        return existing, False
    run_id = uuid.uuid4()
    try:
        inputs = build_run_inputs(plan_snapshot, run_id=str(run_id))
    except RunInputError as exc:
        raise ApiError(422, "RUN_INPUT_INVALID", str(exc)) from exc
    _check_targets(inputs)
    for _ in range(SEQUENCE_RETRIES):
        try:
            return _insert_run(project, plan_snapshot, created_by, run_id, inputs, kind=kind, parent_run=parent_run,
                               change_note=change_note, key=key), True
        except IntegrityError:
            if key and (existing := _existing_by_key(project, key, plan_snapshot)):
                return existing, False  # 같은 요청이 동시에 들어와 다른 쪽이 먼저 만들었다
    raise ApiError(409, "RUN_CONFLICT", "잠시 뒤에 다시 시도해주세요.")


# ---------- 취소 ----------

def request_cancel(run):
    """실행 취소를 요청한다. 아직 워커가 잡지 않은 실행은 바로 취소하고, 진행 중이면 워커가 프로세스를 멈춘다."""
    now = timezone.now()
    with transaction.atomic():
        run = Run.objects.select_for_update().get(pk=run.pk)
        if run.status not in Run.ACTIVE_STATUSES:
            raise ApiError(409, "RUN_NOT_ACTIVE", "이미 끝난 실행은 취소할 수 없어요.")
        if run.cancel_requested_at is None:
            run.cancel_requested_at = now
        if run.status == Run.Status.QUEUED:  # 워커가 아직 시작하지 않았다
            run.status, run.finished_at = Run.Status.CANCELLED, now
            run.task_runs.filter(status=TaskRun.Status.QUEUED).update(status=TaskRun.Status.CANCELLED, finished_at=now)
        run.save(update_fields=["cancel_requested_at", "status", "finished_at"])
    return run
