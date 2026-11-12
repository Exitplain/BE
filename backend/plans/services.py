"""테스트 계획 업무 규칙: 생성 → 수정(If-Match) → 확정(스냅샷 + 페르소나)."""
from django.db import transaction

from common.exceptions import ApiError, validation_error
from common.projects import project_target
from discoveries.models import Discovery

from . import catalog
from .models import PlanSnapshot, TestPlan


# ---------- 공통 ----------

def parse_if_match(header):
    """If-Match: "3" / W/"3" / 3 → 3. 없으면 428."""
    if not header:
        raise ApiError(428, "PRECONDITION_REQUIRED", "If-Match 헤더에 현재 revision을 넣어주세요.")
    value = header.strip()
    if value.startswith("W/"):
        value = value[2:]
    value = value.strip('"')
    if not value.isdigit():
        raise ApiError(400, "INVALID_IF_MATCH", "If-Match 값은 revision 숫자여야 합니다.")
    return int(value)


def _check_revision(plan, revision):
    if revision != plan.revision:
        raise ApiError(409, "REVISION_CONFLICT", "다른 곳에서 계획이 바뀌었습니다. 최신 내용을 다시 불러와주세요.")


def _locked(plan_id):
    return TestPlan.objects.select_for_update().select_related("discovery", "project").get(pk=plan_id)


def _save(plan, fields):
    plan.revision += 1
    plan.save(update_fields=[*fields, "revision", "updated_at"])
    return plan


# ---------- 생성 (1단계 결과로 계획 시작) ----------

def create_plan(user, project, discovery_id):
    discovery = Discovery.objects.filter(pk=discovery_id, project=project).first()
    if discovery is None:
        raise validation_error("discovery_id", "이 프로젝트의 탐색 결과가 아닙니다.")
    if discovery.is_expired:
        raise ApiError(409, "DISCOVERY_EXPIRED", "사이트 확인 결과가 만료됐습니다. 1단계에서 다시 확인해주세요.")
    task_ids = [t["id"] for t in discovery.tasks if t.get("suggested")]
    return TestPlan.objects.create(
        project=project, discovery=discovery, created_by=user,
        task_ids=task_ids,
        task_funnels=[catalog.default_funnel(t) for t in task_ids],
        segments=[dict(s) for s in catalog.DEFAULT_SEGMENTS],
        limits=dict(catalog.DEFAULT_LIMITS),
    )


# ---------- 수정 (2단계·3단계) ----------

def _apply_tasks(plan, task_ids):
    available = set(plan.discovery.task_map())
    unknown = [t for t in task_ids if t not in available]
    if unknown:
        raise validation_error("task_ids", f"선택할 수 없는 과업입니다: {', '.join(unknown)}")
    if len(set(task_ids)) != len(task_ids):
        raise validation_error("task_ids", "같은 과업이 두 번 들어 있습니다.")
    plan.task_ids = task_ids
    # 새 과업에는 초기 퍼널, 뺀 과업의 퍼널은 삭제
    current = {f["task_id"]: f for f in plan.task_funnels}
    plan.task_funnels = [current.get(t) or catalog.default_funnel(t) for t in task_ids]


def _apply_custom_tasks(plan, texts):
    texts = [t.strip() for t in texts]
    if any(not t for t in texts):
        raise validation_error("custom_tasks", "빈 과업은 추가할 수 없습니다.")
    if len(set(texts)) != len(texts):
        raise validation_error("custom_tasks", "같은 과업이 두 번 들어 있습니다.")
    # 자유 입력 과업은 검토 전(draft)이라 실행에 넣지 않는다 (기능명세서 PLAN-02)
    plan.custom_tasks = [{"id": f"custom_{i}", "text": t, "state": "draft"} for i, t in enumerate(texts, 1)]


def _apply_audience(plan, audience):
    ages = audience.get("ages", [])
    if len(set(ages)) != len(ages):
        raise validation_error("audience.ages", "같은 연령대가 두 번 들어 있습니다.")
    if "any" in ages and len(ages) > 1:
        raise validation_error("audience.ages", "전체와 특정 연령대를 함께 선택할 수 없습니다.")
    plan.audience_context = audience["context"]
    plan.ages = ages


def _apply_funnels(plan, funnels):
    by_task = {}
    for i, funnel in enumerate(funnels):
        if funnel["task_id"] in by_task:
            raise validation_error(f"task_funnels.{i}.task_id", "같은 과업의 퍼널이 두 번 들어 있습니다.")
        by_task[funnel["task_id"]] = funnel
    if set(by_task) != set(plan.task_ids):
        raise validation_error("task_funnels", "선택한 과업마다 퍼널을 한 개씩 보내주세요.")

    current = {f["task_id"]: f for f in plan.task_funnels}
    result = []
    for task_id in plan.task_ids:
        field = f"task_funnels.{plan.task_ids.index(task_id)}"
        stages = sorted(by_task[task_id]["stages"], key=lambda s: s["order"])
        current_ids = sorted(s["id"] for s in current[task_id]["stages"])
        if sorted(s["id"] for s in stages) != current_ids:
            # Figma P05b: 이름과 순서만 바꿀 수 있다. 단계 ID는 유지
            raise validation_error(field, "퍼널 단계는 이름과 순서만 바꿀 수 있습니다.")
        if [s["order"] for s in stages] != list(range(len(stages))):
            raise validation_error(field, "단계 순서(order)는 0부터 빠짐없이 매겨주세요.")
        names = [s["name"].strip() for s in stages]
        if any(not n for n in names):
            raise validation_error(field, "단계 이름을 입력해주세요.")
        if len(set(names)) != len(names):
            raise validation_error(field, "같은 과업 안에 단계 이름이 겹칩니다.")  # Figma P05g
        result.append({"task_id": task_id,
                       "stages": [{"id": s["id"], "name": n, "order": s["order"]} for s, n in zip(stages, names)]})
    plan.task_funnels = result


def _apply_segments(plan, segments):
    if [s["id"] for s in segments] != catalog.SEGMENT_IDS:
        raise validation_error("segments", f"세그먼트 {len(catalog.SEGMENT_IDS)}개를 순서대로 보내주세요.")
    total = sum(s["count"] for s in segments)
    if total != catalog.PERSONA_TOTAL:
        # Figma P05e 문구
        raise ApiError(422, "VALIDATION_ERROR",
                       f"합계가 {catalog.PERSONA_TOTAL}명이 되도록 맞춰주세요 · 지금 {total}명",
                       {"segments": [f"지금 {total}명"]})
    plan.segments = [{"id": s["id"], "count": s["count"]} for s in segments]


@transaction.atomic
def update_plan(plan_id, revision, data):
    plan = _locked(plan_id)
    _check_revision(plan, revision)
    fields = set()
    if "task_ids" in data:
        _apply_tasks(plan, data["task_ids"])
        fields |= {"task_ids", "task_funnels"}
    if "custom_tasks" in data:
        _apply_custom_tasks(plan, data["custom_tasks"])
        fields.add("custom_tasks")
    if "additional_request" in data:
        plan.additional_request = data["additional_request"].strip()
        fields.add("additional_request")
    if "audience" in data:
        _apply_audience(plan, data["audience"])
        fields |= {"audience_context", "ages"}
    if "task_funnels" in data:
        _apply_funnels(plan, data["task_funnels"])
        fields.add("task_funnels")
    if "segments" in data:
        _apply_segments(plan, data["segments"])
        fields.add("segments")
    if "frustration_enabled" in data:
        plan.frustration_enabled = data["frustration_enabled"]
        fields.add("frustration_enabled")
    if "limits" in data:
        plan.limits = {**plan.limits, **data["limits"]}
        fields.add("limits")
    if not fields:
        raise validation_error("non_field_errors", "바꿀 필드를 한 개 이상 보내주세요.")
    return _save(plan, sorted(fields))


@transaction.atomic
def reestimate_funnel(plan_id, revision, task_id):
    """Figma P05b '다시 추정' — 과업 퍼널을 초기값으로 되돌린다."""
    plan = _locked(plan_id)
    _check_revision(plan, revision)
    if task_id not in plan.task_ids:
        raise validation_error("task_id", "선택한 과업이 아닙니다.")
    plan.task_funnels = [catalog.default_funnel(task_id) if f["task_id"] == task_id else f
                         for f in plan.task_funnels]
    return _save(plan, ["task_funnels"])


# ---------- 시작 전 확인 (P05c) ----------

def _age_labels(ages):
    return [catalog.AGE_OPTIONS[a] for a in ages]


def blocking_reasons(plan):
    reasons = []
    if plan.discovery.is_expired:
        reasons.append({"step": 1, "code": "DISCOVERY_EXPIRED", "message": "사이트 확인 결과가 만료됐습니다."})
    if not plan.task_ids:
        reasons.append({"step": 2, "code": "NO_TASKS", "message": "과업을 한 개 이상 골라주세요."})
    if not plan.audience_context.strip():
        reasons.append({"step": 2, "code": "NO_AUDIENCE", "message": "타겟 고객 설명을 입력해주세요."})
    if {f["task_id"] for f in plan.task_funnels} != set(plan.task_ids):
        reasons.append({"step": 3, "code": "FUNNEL_MISMATCH", "message": "과업과 퍼널이 맞지 않습니다."})
    if sum(s["count"] for s in plan.segments) != catalog.PERSONA_TOTAL:
        reasons.append({"step": 3, "code": "SEGMENT_TOTAL", "message": "세그먼트 합계가 맞지 않습니다."})
    return reasons


def review(plan):
    tasks = plan.discovery.task_map()
    personas = sum(s["count"] for s in plan.segments)
    seeds = plan.limits["seed_count"]
    return {
        "site": {"entry_url": plan.discovery.entry_url, "login_mode": plan.discovery.login_mode, "edit_step": 1},
        "tasks": {"names": [tasks[t]["name"] for t in plan.task_ids], "edit_step": 2},
        "audience": {"context": plan.audience_context, "age_labels": _age_labels(plan.ages), "edit_step": 2},
        "not_in_run": {"custom_tasks": plan.custom_tasks, "additional_request": plan.additional_request},
        "funnels": {"stages_per_task": {f["task_id"]: len(f["stages"]) for f in plan.task_funnels}, "edit_step": 3},
        "personas": {"segments": len(plan.segments), "total": personas,
                     "frustration_enabled": plan.frustration_enabled, "edit_step": 3},
        "limits": plan.limits,
        "total_sessions": personas * len(plan.task_ids) * seeds,
        "blocking": blocking_reasons(plan),
    }


# ---------- 확정 ----------

@transaction.atomic
def confirm_plan(plan_id, revision, user):
    from personas.services import create_personas  # personas 앱이 plans를 참조하므로 함수 안에서 import

    plan = _locked(plan_id)
    _check_revision(plan, revision)
    existing = PlanSnapshot.objects.filter(plan=plan, plan_revision=plan.revision).first()
    if existing:
        raise ApiError(409, "ALREADY_CONFIRMED", "이 revision은 이미 확정했습니다.",
                       {"plan_revision_id": [str(existing.id)]})
    reasons = blocking_reasons(plan)
    if reasons:
        raise ApiError(409, reasons[0]["code"], reasons[0]["message"],
                       {"blocking": [r["code"] for r in reasons]})

    tasks = plan.discovery.task_map()
    funnels = {f["task_id"]: f["stages"] for f in plan.task_funnels}
    data = {
        "schema_version": "plan-snapshot-1",
        "plan_id": str(plan.id),
        "plan_revision": plan.revision,
        "project_id": plan.project_id,
        "site": {"entry_url": plan.discovery.entry_url, "login_mode": plan.discovery.login_mode,
                 "variant": plan.discovery.variant,
                 "allowed_origins": project_target(plan.project)["allowed_origins"],
                 "discovery_id": str(plan.discovery_id)},
        "features": plan.discovery.features,
        "tasks": [{**tasks[t], "funnel": funnels[t]} for t in plan.task_ids],
        "not_in_run": {"custom_tasks": plan.custom_tasks, "additional_request": plan.additional_request},
        "audience": {"context": plan.audience_context, "ages": plan.ages, "age_labels": _age_labels(plan.ages),
                     "age_usage": "context_only"},
        "frustration_enabled": plan.frustration_enabled,
        "limits": dict(plan.limits),
    }
    snapshot = PlanSnapshot(plan=plan, plan_revision=plan.revision, data=data,
                            persona_generator="", confirmed_by=user)
    snapshot.save()
    generator, segments, personas = create_personas(snapshot, plan.audience_context, plan.ages, plan.segments)
    snapshot.persona_generator = generator
    snapshot.data = {
        **data,
        "persona_generator": generator,
        "segments": segments,
        "personas": personas,
        "total_sessions": len(personas) * len(plan.task_ids) * plan.limits["seed_count"],
    }
    snapshot.save(update_fields=["persona_generator", "data"])
    return snapshot


# ---------- 직렬화 ----------

def plan_dict(plan):
    return {
        "id": str(plan.id), "project_id": plan.project_id, "discovery_id": str(plan.discovery_id),
        "revision": plan.revision,
        "options": {"features": plan.discovery.features, "tasks": plan.discovery.tasks,
                    "capabilities": catalog.capabilities()},
        "task_ids": plan.task_ids, "custom_tasks": plan.custom_tasks,
        "additional_request": plan.additional_request,
        "audience": {"context": plan.audience_context, "ages": plan.ages},
        "task_funnels": plan.task_funnels, "segments": plan.segments,
        "frustration_enabled": plan.frustration_enabled, "limits": plan.limits,
        "review": review(plan),
        "snapshots": [{"plan_revision_id": str(s.id), "plan_revision": s.plan_revision,
                       "confirmed_at": s.confirmed_at.isoformat()} for s in plan.snapshots.all()[:10]],
        "created_at": plan.created_at.isoformat(), "updated_at": plan.updated_at.isoformat(),
    }


def snapshot_dict(snapshot):
    return {"plan_revision_id": str(snapshot.id), "plan_id": str(snapshot.plan_id),
            "plan_revision": snapshot.plan_revision, "confirmed_at": snapshot.confirmed_at.isoformat(),
            "persona_generator": snapshot.persona_generator, "data": snapshot.data}