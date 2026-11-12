from common.exceptions import ApiError
from plans.catalog import AGE_OPTIONS

from . import generators
from .models import PersonaSnapshot


def persona_context(segment_name, audience_context, ages):
    text = f"{segment_name} · 타겟: {audience_context}"
    if ages and ages != ["any"]:
        text += " · 연령 문맥: " + ", ".join(AGE_OPTIONS.get(a, a) for a in ages)
    return text[:1000]


def persona_dict(p):
    return {"persona_snapshot_id": str(p.id), "code": p.code, "segment_id": p.segment_id,
            "segment_name": p.segment_name, "is_representative": p.is_representative,
            "variables": p.variables, "context": p.context, "generator": p.generator}


def create_personas(plan_snapshot, audience_context, ages, segments):
    """plans.services.confirm_plan()이 확정 트랜잭션 안에서 호출한다.

    반환: (생성기 이름, 세그먼트 요약 목록, 페르소나 dict 목록)
    생성에 실패하면 ApiError를 던져 확정 전체가 롤백된다.
    """
    counts = [s["count"] for s in segments]
    try:
        generator, generated = generators.generate(audience_context, ages, counts)
    except generators.GenerationError as exc:
        raise ApiError(503, "PERSONA_GENERATION_FAILED", f"페르소나를 만들지 못했습니다: {exc}") from exc

    rows, summary, number = [], [], 0
    for segment, info in zip(segments, generated):
        first = None
        for i in range(segment["count"]):
            number += 1
            code = f"P-{number:03d}"
            first = first or code
            rows.append(PersonaSnapshot(
                plan_snapshot=plan_snapshot, code=code, segment_id=segment["id"], segment_name=info["name"],
                is_representative=(i == 0), variables=info["variables"],
                context=persona_context(info["name"], audience_context, ages), generator=generator,
            ))
        summary.append({"id": segment["id"], "name": info["name"], "count": segment["count"],
                        "variables": info["variables"], "representative": first})
    PersonaSnapshot.objects.bulk_create(rows)
    return generator, summary, [persona_dict(p) for p in rows]