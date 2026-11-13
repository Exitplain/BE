"""위저드 선택지·기본값. 줄마다 출처를 적는다.

- Figma: 4호선톤 와이어프레임 P05·P05b·P07 화면 값
- API_SPEC: docs/API_SPEC.md 8절 제품 API 제안
- agent: 저장소 agent/ 패키지 정의를 그대로 참조
"""

# Figma P05 연령대 칩. (현재 개발 API의 any/teens/.../60plus와 다름)
AGE_OPTIONS = {"any": "전체", "20s": "20대", "30s": "30대", "40s": "40대", "50plus": "50대 이상"}

TEXT_LIMIT = 500           # 타겟 설명·추가 메모 (API_SPEC 4.2와 같은 500자)
CUSTOM_TASK_LIMIT = 5
CUSTOM_TASK_TEXT_LIMIT = 100

# Figma P05b: 세그먼트 4개 × 5명 = 20명
SEGMENT_IDS = ["segment_1", "segment_2", "segment_3", "segment_4"]
PERSONA_TOTAL = 20
DEFAULT_SEGMENTS = [{"id": s, "count": 5} for s in SEGMENT_IDS]

# 실행 조건. Figma P05b: 모바일 390 · 동시 5세션 · 요청 간격 1초 · 시드 3회 · 스텝 40 · 5분 · 비용 상한(값 확인 필요)
# 범위는 agent.schemas.RunInput 상한과 같게 둔다 (동시 실행 1~5, 요청 간격 0~5초).
DEFAULT_LIMITS = {
    "viewport_width": 390,
    "concurrency": 5,
    "request_interval_seconds": 1.0,
    "seed_count": 3,
    "max_steps": 40,
    "timeout_seconds": 300,
    "cost_cap": None,
}
LIMIT_RANGES = {  # (최소, 최대, 정수만)
    "viewport_width": (320, 1920, True),
    "concurrency": (1, 5, True),
    "request_interval_seconds": (0.0, 5.0, False),
    "seed_count": (1, 3, True),
    "max_steps": (1, 100, True),
    "timeout_seconds": (30, 600, True),
}

# 퍼널: Figma P05c "사이트 분석으로 자동 결정 · 과업별 7단계". 위저드에서 편집하지 않는다.
# 단계는 에이전트(agent/funnel.py)가 화면 경로로 정하는 7단계와 같다.
def default_funnel(task_id):
    from agent.funnel import FUNNEL
    return {"task_id": task_id, "auto": True,
            "stages": [{"id": f"stage_{key}", "name": name, "order": i} for i, (key, name) in enumerate(FUNNEL)]}


def capabilities():
    """API_SPEC 8절: 허용 범위는 BE가 돌려준 capability로 검증한다."""
    return {
        "ages": AGE_OPTIONS,
        "segment_ids": SEGMENT_IDS,
        "persona_total": PERSONA_TOTAL,
        "limits": {k: {"min": lo, "max": hi} for k, (lo, hi, _) in LIMIT_RANGES.items()},
        "custom_task_limit": CUSTOM_TASK_LIMIT,
    }