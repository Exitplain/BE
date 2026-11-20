"""실행 조회(읽기 전용): 진행 상황(P08~P10), 세션 타일(P11), 이벤트 폴링(think-aloud).

프런트가 2~3초마다 부르므로 쿼리 수는 항목 수와 무관하게 일정해야 한다 (annotate·select_related·일괄 조회).
응답에는 서버 파일 경로를 넣지 않고, 스크린샷은 소유자만 볼 수 있는 URL 로 바꿔 준다.
"""
from django.db.models import Count, OuterRef, Subquery
from django.urls import reverse

from . import cursors, report_link
from .models import Observation, Run, RunEvent, Session, TaskRun
from .services import _iso, run_item


def observation_url(observation_id):
    return reverse("observation-screenshot", args=[observation_id])


def final_screenshot_url(session_id):
    return reverse("session-final-screenshot", args=[session_id])


def run_phase(status, started_sessions):
    """Figma 단계: 준비 중(P08) → 실행 중(P09) → 분석 중(P10) → 끝."""
    if status == Run.Status.QUEUED:
        return "preparing"
    if status == Run.Status.RUNNING:
        return "running" if started_sessions else "preparing"
    if status == Run.Status.FINALIZING:
        return "analyzing"
    if status in (Run.Status.COMPLETED, Run.Status.PARTIALLY_COMPLETED):
        return "done"
    return status  # failed / cancelled


def _counts(queryset):
    return dict(queryset.order_by().values_list("status").annotate(n=Count("id")))


# ---------- 과업 진행 순서 ----------

def task_order(run):
    """계획에서 고른 과업 순서 {과업 id: 순번}. 워커는 이 순서(왼쪽 → 오른쪽)로, 과업마다 시드 1부터 돌린다."""
    tasks = ((run.plan_snapshot.data or {}).get("tasks") or []) if run.plan_snapshot_id else []
    return {t["id"]: i for i, t in enumerate(tasks) if isinstance(t, dict) and t.get("id")}


def ordered_task_runs(run, task_runs):
    order = task_order(run)
    return sorted(task_runs, key=lambda t: (order.get(t.task_key, len(order)), t.seed, t.pk))


TASK_PHASE = {"running": "running", "queued": "queued", "failed": "failed", "cancelled": "cancelled"}


def task_progress(run):
    """과업 탭에 그대로 쓰는 진행 순서와 상태. 시드가 여러 개면 하나라도 실행 중이면 running,
    모두 끝났으면 done(하나라도 실패면 failed), 하나도 시작 안 했으면 queued."""
    names = {t.get("id"): t.get("name") for t in ((run.plan_snapshot.data or {}).get("tasks") or [])
             if isinstance(t, dict)} if run.plan_snapshot_id else {}
    groups = {}
    for task_run in ordered_task_runs(run, TaskRun.objects.filter(run=run)):
        groups.setdefault(task_run.task_key, []).append(task_run.status)
    out = []
    for position, (key, statuses) in enumerate(groups.items(), start=1):
        if TaskRun.Status.RUNNING in statuses:
            phase = "running"
        elif all(s == TaskRun.Status.QUEUED for s in statuses):
            phase = "queued"
        elif any(s == TaskRun.Status.QUEUED for s in statuses):
            phase = "running"                        # 시드 일부만 끝남
        elif TaskRun.Status.FAILED in statuses:
            phase = "failed"
        elif all(s == TaskRun.Status.CANCELLED for s in statuses):
            phase = "cancelled"
        else:
            phase = "done"
        out.append({"task_id": key, "name": names.get(key) or key, "position": position, "status": phase,
                    "seeds": len(statuses)})
    return out


def run_detail(run):
    sessions = _counts(Session.objects.filter(run=run))
    task_runs = _counts(TaskRun.objects.filter(run=run))
    started = sum(sessions.values())
    reports = report_link.latest_report_summaries([run.id])
    item = run_item(run, reports.get(str(run.id)))
    item.update({
        "project_id": run.project_id,
        "phase": run_phase(run.status, started),
        "cancel_requested": run.cancel_requested_at is not None,
        # 이 실행이 쓴 보정 리비전 (0 = 기본 모형, CAL-04)
        "calibration_revision": (run.config or {}).get("calibration_revision", 0),
        "error": {"code": run.error_code, "message": run.error_message} if run.error_code else None,
        # 과업 진행 순서 (계획 순서 = 실행 순서). 화면은 이 순서대로 왼쪽 → 오른쪽에 둔다
        "tasks": task_progress(run),
        "progress": {
            "expected_sessions": (run.config or {}).get("expected_sessions", 0),
            "started_sessions": started,
            "finished_sessions": started - sessions.get(Session.Status.RUNNING, 0),
            "sessions_by_status": sessions,
            "task_runs": {"total": sum(task_runs.values()), **{s: task_runs.get(s, 0) for s in TaskRun.Status.values}},
        },
    })
    return item


# ---------- 세션 타일 (P11) ----------

def _explanation(session):
    """왜 끝났는지 한 줄 (agent/explain.py). 진행 중이면 None."""
    if not session.is_terminal:
        return None
    from agent.explain import describe
    return describe(session.status, session.reason, session.issues or [], session.terminal_stage)


def _session_item(session):
    persona = session.persona_snapshot
    latest = None
    if getattr(session, "lo_id", None) is not None:
        latest = {"id": session.lo_id, "step": session.lo_step, "url": session.lo_url,
                  "screenshot_url": observation_url(session.lo_id) if session.lo_artifact else None}
    return {
        "id": session.pk, "session_key": session.session_key,
        "persona": {"code": persona.code, "segment_id": persona.segment_id, "segment_name": persona.segment_name},
        "task_id": session.task_id, "seed": session.seed, "status": session.status, "reason": session.reason,
        "explanation": _explanation(session),
        "steps": session.steps, "duration_seconds": session.duration_seconds, "max_frustration": session.max_frustration,
        "terminal_stage": session.terminal_stage, "stages_reached": session.stages_reached,
        "latest_observation": latest,
        "final_screenshot_url": final_screenshot_url(session.pk) if session.final_screenshot else None,
        "started_at": _iso(session.started_at), "finished_at": _iso(session.finished_at),
    }


def list_sessions(run, *, statuses=(), limit=50, cursor=""):
    latest = Observation.objects.filter(session=OuterRef("pk")).order_by("-step")
    queryset = (Session.objects.filter(run=run).select_related("persona_snapshot").annotate(
        lo_id=Subquery(latest.values("id")[:1]), lo_step=Subquery(latest.values("step")[:1]),
        lo_url=Subquery(latest.values("url")[:1]), lo_artifact=Subquery(latest.values("artifact")[:1])))
    if statuses:
        queryset = queryset.filter(status__in=statuses)
    total = queryset.count()
    if cursor:
        queryset = queryset.filter(pk__gt=cursors.decode(cursor)[0])
    rows = list(queryset.order_by("pk")[: limit + 1])  # 시작한 순서. 새 세션은 뒤에 붙는다
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {"items": [_session_item(s) for s in rows], "next_cursor": cursors.encode(rows[-1].pk) if has_more else None,
            "total": total}


# ---------- 이벤트 폴링 ----------

def _public_payload(event, observation_ids):
    payload = dict(event.payload or {})
    payload.pop("artifact", None)  # 서버 파일 경로는 내보내지 않는다
    final = payload.pop("final_artifact", None)
    if isinstance(payload.get("issues"), list):
        payload["issues"] = [{k: v for k, v in issue.items() if k != "artifact"} if isinstance(issue, dict) else issue
                             for issue in payload["issues"]]
    if event.type == "observation.created":
        observation_id = observation_ids.get((event.session_id, payload.get("step_id")))
        payload["screenshot_url"] = observation_url(observation_id) if observation_id else None
    elif event.type == "session.finished" and event.session_id:
        payload["screenshot_url"] = final_screenshot_url(event.session_id) if final and event.session.final_screenshot else None
    return payload


def list_events(run, *, after=0, limit=100):
    """`id > after` 인 이벤트를 id 순서로. 프런트는 next_after 를 다음 호출의 after 로 넘긴다."""
    rows = list(RunEvent.objects.filter(run=run, id__gt=after).select_related("session").order_by("id")[: limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]
    watched = [e for e in rows if e.type == "observation.created" and e.session_id]
    observation_ids = {}
    if watched:
        observation_ids = {(o.session_id, o.step_id): o.pk for o in Observation.objects.filter(
            session_id__in={e.session_id for e in watched}, step_id__in={(e.payload or {}).get("step_id") for e in watched}
        ).exclude(artifact="")}
    items = [{"id": e.pk, "type": e.type, "sequence": e.sequence, "session_id": e.session_id,
              "session_key": e.session.session_key if e.session_id else None, "occurred_at": _iso(e.occurred_at),
              "payload": _public_payload(e, observation_ids)} for e in rows]
    return {"items": items, "next_after": rows[-1].pk if rows else after, "has_more": has_more}
