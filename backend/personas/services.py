import random

from common.exceptions import ApiError
from plans.catalog import AGE_OPTIONS

from . import generators
from .models import PersonaSnapshot


# Figma P07 세그먼트 카드의 픽셀 캐릭터 (FE public/personas/persona-<키>.svg). 연령대별 4종
AVATARS = ("20s", "30s", "40s", "50plus")


def pick_avatar(age_group, ages, index):
    """세그먼트 캐릭터. 생성기가 준 연령대가 있으면 그것, 없으면 고른 연령대(없으면 전체)를 세그먼트 순서대로 돌려 쓴다."""
    if age_group in AVATARS:
        return age_group
    picked = [a for a in (ages or []) if a in AVATARS] or list(AVATARS)
    return picked[index % len(picked)]


def persona_context(segment_name, audience_context, ages, situation=None, age_group=None):
    """에이전트에 넘기는 페르소나 설명. LLM 생성기는 세그먼트마다 상황·연령대를 준다 (연령은 문맥으로만)."""
    text = f"{segment_name} · {situation} · 타겟: {audience_context}" if situation else f"{segment_name} · 타겟: {audience_context}"
    if age_group:
        text += " · 연령 문맥: " + AGE_OPTIONS.get(age_group, age_group)
    elif ages and ages != ["any"]:
        text += " · 연령 문맥: " + ", ".join(AGE_OPTIONS.get(a, a) for a in ages)
    return text[:1000]


def persona_dict(p):
    return {"persona_snapshot_id": str(p.id), "code": p.code, "segment_id": p.segment_id,
            "segment_name": p.segment_name, "is_representative": p.is_representative,
            "variables": p.variables, "context": p.context, "generator": p.generator, "avatar": p.avatar}


def _generate(audience_context, ages, segments):
    counts = [s["count"] for s in segments]
    try:
        return generators.generate(audience_context, ages, counts)
    except generators.GenerationError as exc:
        raise ApiError(503, "PERSONA_GENERATION_FAILED", f"페르소나를 만들지 못했습니다: {exc}") from exc


def infer_segment_profiles(audience_context, ages, segments):
    """3단계 세그먼트 유형 추론 → TestPlan.segment_profiles 에 그대로 저장하는 모양."""
    generator, generated = _generate(audience_context, ages, segments)
    items = [{"id": segment["id"], "name": info["name"], "situation": info.get("situation"),
              "age_group": info.get("age_group"), "variables": info["variables"], "basis": info.get("basis", []),
              "avatar": pick_avatar(info.get("age_group"), ages, index)}
             for index, (segment, info) in enumerate(zip(segments, generated))]
    return {"generator": generator, "items": items}


SPREAD = 8          # 같은 세그먼트 안 개인차 (표준편차). 대표 1명은 세그먼트 기본값 그대로
LOW, HIGH = 5, 95


def individual_variables(base, segment_id, index):
    """같은 유형 안에서도 사람마다 조금씩 다르게. (세그먼트, 순번)으로 정해져 같은 계획이면 항상 같은 값."""
    if index == 0:
        return dict(base)
    rng = random.Random(f"{segment_id}:{index}")
    return {k: max(LOW, min(HIGH, round(v + rng.gauss(0, SPREAD)))) for k, v in base.items()}


def create_personas(plan_snapshot, audience_context, ages, segments, profiles=None):
    """plans.services.confirm_plan()이 확정 트랜잭션 안에서 호출한다.

    profiles: 3단계에서 추론해 둔 세그먼트 유형 (infer_segment_profiles). 있으면 다시 만들지 않는다.
    반환: (생성기 이름, 세그먼트 요약 목록, 페르소나 dict 목록)
    생성에 실패하면 ApiError를 던져 확정 전체가 롤백된다.
    """
    items = {p["id"]: p for p in (profiles or {}).get("items", [])}
    if profiles and all(s["id"] in items for s in segments):
        generator, generated = profiles["generator"], [items[s["id"]] for s in segments]
    else:
        generator, generated = _generate(audience_context, ages, segments)

    rows, summary, number = [], [], 0
    for index, (segment, info) in enumerate(zip(segments, generated)):
        avatar = info.get("avatar") or pick_avatar(info.get("age_group"), ages, index)
        first = None
        for i in range(segment["count"]):
            number += 1
            code = f"P-{number:03d}"
            first = first or code
            rows.append(PersonaSnapshot(
                plan_snapshot=plan_snapshot, code=code, segment_id=segment["id"], segment_name=info["name"],
                is_representative=(i == 0), variables=individual_variables(info["variables"], segment["id"], i),
                context=persona_context(info["name"], audience_context, ages, info.get("situation"), info.get("age_group")),
                generator=generator, avatar=avatar,
            ))
        summary.append({"id": segment["id"], "name": info["name"], "count": segment["count"],
                        "variables": info["variables"], "basis": info.get("basis", []),   # 수치를 정한 근거
                        "situation": info.get("situation"), "age_group": info.get("age_group"),
                        "representative": first, "avatar": avatar})
    PersonaSnapshot.objects.bulk_create(rows)
    return generator, summary, [persona_dict(p) for p in rows]