"""테스트 계획 업무 규칙: 생성 → 수정(If-Match) → 확정(스냅샷 + 페르소나)."""
from urllib.parse import urlsplit

from django.conf import settings
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


def _check_discovery(plan):
    if plan.discovery.is_expired:
        raise ApiError(409, "DISCOVERY_EXPIRED", "사이트 확인 결과가 만료됐습니다. 1단계에서 다시 확인해주세요.")


def _locked(plan_id):
    return TestPlan.objects.select_for_update().select_related("discovery", "project").get(pk=plan_id)


def _save(plan, fields):
    plan.revision += 1
    plan.save(update_fields=[*fields, "revision", "updated_at"])
    return plan


# ---------- 페르소나 인원 ----------

def uses_test_accounts(plan):
    return plan.discovery.login_mode == Discovery.LoginMode.TEST_ACCOUNT


def persona_range(plan):
    """확정할 수 있는 페르소나 수 (최소, 최대).
    로그인 없이 테스트하면 4~20명 (기본 10명), 테스트 계정으로 테스트하면 정확히 계정 수 (Figma P05h)."""
    if uses_test_accounts(plan):
        from testaccounts.services import account_count
        count = account_count(plan.project)
        return count, count
    return catalog.PERSONA_MIN, catalog.PERSONA_MAX


def persona_total(plan):
    """화면에 보여 줄 기준 인원. 테스트 계정이면 계정 수, 아니면 기본 10명."""
    if uses_test_accounts(plan):
        return persona_range(plan)[0]
    return catalog.PERSONA_DEFAULT


def _total_ok(plan, total):
    low, high = persona_range(plan)
    return low <= total <= high


def split_segments(total):
    """세그먼트 4개에 인원을 고르게 나눈다. 예: 10 → 3, 3, 2, 2"""
    n = len(catalog.SEGMENT_IDS)
    return [{"id": s, "count": total // n + (1 if i < total % n else 0)} for i, s in enumerate(catalog.SEGMENT_IDS)]


# ---------- 생성 (1단계 결과로 계획 시작) ----------

def create_plan(user, project, discovery_id):
    discovery = Discovery.objects.filter(pk=discovery_id, project=project).first()
    if discovery is None:
        raise validation_error("discovery_id", "이 프로젝트의 탐색 결과가 아닙니다.")
    if discovery.status != Discovery.Status.SUCCEEDED:
        raise ApiError(409, "DISCOVERY_NOT_READY", "사이트 확인이 끝나지 않았거나 실패했습니다. 결과를 확인한 뒤 진행해주세요.")
    if discovery.is_expired:
        raise ApiError(409, "DISCOVERY_EXPIRED", "사이트 확인 결과가 만료됐습니다. 1단계에서 다시 확인해주세요.")
    task_ids = [t["id"] for t in discovery.tasks if t.get("suggested")]
    if discovery.login_mode == Discovery.LoginMode.TEST_ACCOUNT:
        from testaccounts.services import account_count
        segments = split_segments(account_count(project))
    else:
        segments = [dict(s) for s in catalog.DEFAULT_SEGMENTS]
    return TestPlan.objects.create(
        project=project, discovery=discovery, created_by=user,
        task_ids=task_ids,
        task_funnels=[catalog.default_funnel(t) for t in task_ids],
        segments=segments,
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
    plan.task_funnels = [catalog.default_funnel(t) for t in task_ids]   # 과업마다 자동 7단계


def _apply_custom_tasks(plan, texts):
    texts = [t.strip() for t in texts]
    if any(not t for t in texts):
        raise validation_error("custom_tasks", "빈 과업은 추가할 수 없습니다.")
    if len(set(texts)) != len(texts):
        raise validation_error("custom_tasks", "같은 과업이 두 번 들어 있습니다.")
    # 자유 입력 과업은 저장 때는 검토 전(draft)이다. 확정할 때 목표·성공 기준으로 바꿔 실행에 넣는다
    # (기능명세서 PLAN-02, _custom_run_tasks). EXITPLAIN_RUN_CUSTOM_TASKS 를 끄면 예전처럼 실행에 넣지 않는다
    plan.custom_tasks = [{"id": f"custom_{i}", "text": t, "state": "draft"} for i, t in enumerate(texts, 1)]


def runs_custom_tasks(plan):
    return bool(plan.custom_tasks) and getattr(settings, "EXITPLAIN_RUN_CUSTOM_TASKS", False)


def _custom_start(plan):
    """직접 추가한 과업의 시작 화면. 데모 쇼핑몰 과업은 case 로, 사이트 확인(explore) 과업은 start_path 로 연다
    (plans/run_input.py _target_url). 확인 결과 과업 중 start_path 가 하나라도 있으면 explore 로 본다."""
    if any(t.get("start_path") for t in plan.discovery.tasks or []):
        return {"start_path": urlsplit(plan.discovery.entry_url).path or "/", "case": None}
    return {"case": "all"}


def _custom_run_tasks(plan):
    """직접 추가한 과업 → 실행할 과업 (PLAN-02).

    사용자 문장은 할 일이라 판정 모델이 성공을 확인하기 어렵다. agent/criteria.py 가 모델로 "목표에 도달했을 때
    화면에 보이는 것"을 한 문장으로 다듬고, 모델을 못 쓰면 정해진 문장 틀을 쓴다 (확정이 막히지 않음).
    success_path 가 없으므로 워커가 이 과업만 AGENT_CRITERIA_POLICY(claude)로 돌린다 (runs/worker.py).
    """
    if not runs_custom_tasks(plan):
        return []
    from agent.criteria import draft_criteria
    drafts = draft_criteria([{"id": c["id"], "text": c["text"]} for c in plan.custom_tasks],
                            entry_url=plan.discovery.entry_url, screen_names=plan.discovery.screen_names)
    start = _custom_start(plan)
    return [{"id": c["id"], "name": c["text"][:60], "custom": True, "feature_id": None, "suggested": False, **start,
             "goal": drafts[c["id"]]["goal"], "success_path": None,
             "success_criteria": drafts[c["id"]]["success_criteria"],
             "criteria_source": drafts[c["id"]]["criteria_source"],
             "funnel": catalog.default_funnel(c["id"])["stages"]}
            for c in plan.custom_tasks]


def _apply_audience(plan, audience):
    ages = audience.get("ages", [])
    if len(set(ages)) != len(ages):
        raise validation_error("audience.ages", "같은 연령대가 두 번 들어 있습니다.")
    if "any" in ages and len(ages) > 1:
        raise validation_error("audience.ages", "전체와 특정 연령대를 함께 선택할 수 없습니다.")
    plan.audience_context = audience["context"]
    plan.ages = ages


def _apply_segments(plan, segments):
    if [s["id"] for s in segments] != catalog.SEGMENT_IDS:
        raise validation_error("segments", f"세그먼트 {len(catalog.SEGMENT_IDS)}개를 순서대로 보내주세요.")
    total = sum(s["count"] for s in segments)
    if not _total_ok(plan, total):
        low, high = persona_range(plan)
        # Figma P05e 문구 (테스트 계정이면 계정 수에 맞춘다, P05h)
        message = (f"합계가 {low}명이 되도록 맞춰주세요 · 지금 {total}명" if low == high
                   else f"합계를 {low}~{high}명 사이로 맞춰주세요 · 지금 {total}명")
        raise ApiError(422, "VALIDATION_ERROR", message, {"segments": [f"지금 {total}명"]})
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
        before = (plan.audience_context, list(plan.ages))
        _apply_audience(plan, data["audience"])
        fields |= {"audience_context", "ages"}
        if (plan.audience_context, plan.ages) != before and plan.segment_profiles:
            plan.segment_profiles = {}                   # 타겟이 바뀌면 추론한 세그먼트 유형은 다시 만든다
            fields.add("segment_profiles")
    if "segments" in data:
        _apply_segments(plan, data["segments"])
        fields.add("segments")
    if "limits" in data:
        plan.limits = {**plan.limits, **data["limits"]}
        fields.add("limits")
    if not fields:
        raise validation_error("non_field_errors", "바꿀 필드를 한 개 이상 보내주세요.")
    return _save(plan, sorted(fields))


# ---------- 세그먼트 유형 추론 (3단계) ----------

def infer_segments(plan_id, revision):
    """타겟 설명·연령대로 세그먼트 유형(이름·상황·기본 능력치)을 미리 만든다. 다시 부르면 새로 만든다."""
    from personas.services import infer_segment_profiles
    plan = _locked(plan_id)
    _check_revision(plan, revision)
    if not plan.audience_context.strip():
        raise validation_error("audience.context", "타겟 고객 설명을 먼저 적어 주세요.")
    plan.segment_profiles = infer_segment_profiles(plan.audience_context, plan.ages, plan.segments)
    return _save(plan, ["segment_profiles"])


def segment_view(plan):
    """화면용 세그먼트: 인원(segments)에 추론한 유형(segment_profiles)을 붙인다. 추론 전이면 name 이 None."""
    items = {p["id"]: p for p in (plan.segment_profiles or {}).get("items", [])}
    keys = ("name", "situation", "age_group", "variables", "basis", "avatar")
    return [{**s, **{k: items.get(s["id"], {}).get(k) for k in keys}} for s in plan.segments]


# ---------- 시작 전 확인 (P05c) ----------

def _site_origins(plan):
    """에이전트가 다닐 수 있는 출처. 코드로 확인했으면 그 미리보기 출처 하나뿐이다."""
    if plan.discovery.source_snapshot_id:
        from sources.preview import origin_for
        return [origin_for(plan.discovery.source_snapshot_id)]
    return project_target(plan.project)["allowed_origins"]


def _site_source(plan):
    snapshot = plan.discovery.source_snapshot
    if snapshot is None:
        return None
    return {"snapshot_id": str(snapshot.pk), "commit_sha": snapshot.commit_sha, "tree_digest": snapshot.tree_digest,
            "provider": snapshot.connection.provider}


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
        reasons.append({"step": 3, "code": "FUNNEL_MISMATCH", "message": "과업과 단계별 이동이 맞지 않습니다."})
    if uses_test_accounts(plan):
        from testaccounts.services import MIN_ACCOUNTS, account_count
        if account_count(plan.project) < MIN_ACCOUNTS:
            reasons.append({"step": 1, "code": "TEST_ACCOUNTS_REQUIRED",
                            "message": f"테스트 계정을 {MIN_ACCOUNTS}개 이상 등록해주세요."})
    if not _total_ok(plan, sum(s["count"] for s in plan.segments)):
        reasons.append({"step": 3, "code": "SEGMENT_TOTAL", "message": "세그먼트 합계가 맞지 않습니다."})
    return reasons


def review(plan):
    tasks = plan.discovery.task_map()
    personas = sum(s["count"] for s in plan.segments)
    seeds = plan.limits["seed_count"]
    custom_run = runs_custom_tasks(plan)
    custom_names = [c["text"] for c in plan.custom_tasks] if custom_run else []
    return {
        "site": {"entry_url": plan.discovery.entry_url, "login_mode": plan.discovery.login_mode, "edit_step": 1},
        "tasks": {"names": [tasks[t]["name"] for t in plan.task_ids], "custom_names": custom_names, "edit_step": 2},
        "audience": {"context": plan.audience_context, "age_labels": _age_labels(plan.ages), "edit_step": 2},
        "not_in_run": {"custom_tasks": [] if custom_run else plan.custom_tasks,
                       "additional_request": plan.additional_request},
        "funnels": {"auto": True, "stages_per_task": {f["task_id"]: len(f["stages"]) for f in plan.task_funnels}},
        "personas": {"segments": len(plan.segments), "total": personas, "edit_step": 3},
        "limits": plan.limits,
        "total_sessions": personas * (len(plan.task_ids) + len(custom_names)) * seeds,
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
    funnels = {t: catalog.default_funnel(t)["stages"] for t in plan.task_ids}
    custom = _custom_run_tasks(plan)        # 모델 호출이 있을 수 있다 (agent/criteria.py, 실패해도 막지 않음)
    data = {
        "schema_version": "plan-snapshot-1",
        "plan_id": str(plan.id),
        "plan_revision": plan.revision,
        "project_id": plan.project_id,
        "site": {"entry_url": plan.discovery.entry_url, "login_mode": plan.discovery.login_mode,
                 "variant": plan.discovery.variant,
                 "allowed_origins": _site_origins(plan),
                 "discovery_id": str(plan.discovery_id),
                 # 코드로 확인했으면 어떤 코드로 실행하는지 고정해 남긴다 (SRC-02)
                 "source": _site_source(plan)},
        "features": plan.discovery.features,
        "screen_names": dict(plan.discovery.screen_names or {}),   # 화면 역할 이름 (리포트·세션 표시용, 에이전트에는 안 넘김)
        "tasks": [{**tasks[t], "funnel": funnels[t]} for t in plan.task_ids] + custom,
        "not_in_run": {"custom_tasks": [] if custom else plan.custom_tasks,
                       "additional_request": plan.additional_request},
        "audience": {"context": plan.audience_context, "ages": plan.ages, "age_labels": _age_labels(plan.ages),
                     "age_usage": "context_only"},
        "frustration_enabled": True,     # 토글이 Figma에서 빠짐. 예전 스냅샷과 같은 모양을 위해 남긴다
        "limits": dict(plan.limits),
    }
    snapshot = PlanSnapshot(plan=plan, plan_revision=plan.revision, data=data,
                            persona_generator="", confirmed_by=user)
    snapshot.save()
    generator, segments, personas = create_personas(snapshot, plan.audience_context, plan.ages, plan.segments,
                                                    profiles=plan.segment_profiles or None)
    snapshot.persona_generator = generator
    if uses_test_accounts(plan):
        from testaccounts.services import snapshot_accounts
        data["test_accounts"] = snapshot_accounts(plan.project, personas)   # 페르소나 ↔ 계정 짝 (비밀번호 없음)
    snapshot.data = {
        **data,
        "persona_generator": generator,
        "segments": segments,
        "personas": personas,
        "total_sessions": len(personas) * len(data["tasks"]) * plan.limits["seed_count"],
    }
    snapshot.save(update_fields=["persona_generator", "data"])
    return snapshot


# ---------- 직렬화 ----------

def plan_dict(plan):
    return {
        "id": str(plan.id), "project_id": plan.project_id, "discovery_id": str(plan.discovery_id),
        "revision": plan.revision,
        "options": {"features": plan.discovery.features, "tasks": plan.discovery.tasks,
                    "capabilities": {**catalog.capabilities(), "persona_total": persona_total(plan),
                                     "persona_min": persona_range(plan)[0], "persona_max": persona_range(plan)[1]}},
        "task_ids": plan.task_ids, "custom_tasks": plan.custom_tasks,
        "additional_request": plan.additional_request,
        "audience": {"context": plan.audience_context, "ages": plan.ages},
        "task_funnels": [catalog.default_funnel(t) for t in plan.task_ids],   # 보기 전용 (자동 7단계)
        "segments": plan.segments, "limits": plan.limits,
        "segment_profiles": segment_view(plan),        # 3단계에서 추론한 세그먼트 유형 (추론 전이면 name 이 None)
        "segment_generator": (plan.segment_profiles or {}).get("generator"),
        "review": review(plan),
        "snapshots": [{"plan_revision_id": str(s.id), "plan_revision": s.plan_revision,
                       "confirmed_at": s.confirmed_at.isoformat()} for s in plan.snapshots.all()[:10]],
        "created_at": plan.created_at.isoformat(), "updated_at": plan.updated_at.isoformat(),
    }


def snapshot_dict(snapshot):
    return {"plan_revision_id": str(snapshot.id), "plan_id": str(snapshot.plan_id),
            "plan_revision": snapshot.plan_revision, "confirmed_at": snapshot.confirmed_at.isoformat(),
            "persona_generator": snapshot.persona_generator, "data": snapshot.data}