"""세그먼트 생성기 (Figma P06 → P07).

LLM 담당의 생성 함수가 오면 settings.EXITPLAIN_PERSONA_GENERATOR에 "모듈.함수" 경로를 넣는다.

    generate(audience_context: str, ages: list[str], segment_counts: list[int]) -> list[dict]
    # 세그먼트 수만큼 [{"name": "빠른 구매형",
    #                   "variables": {"patience": 22, "digital_literacy": 80,
    #                                 "price_sensitivity": 40, "trust_need": 30}}, ...]

기본값(fixture)은 타겟 설명과 관계없이 Figma P07 예시 값을 돌려준다.
"""
from django.conf import settings
from django.utils.module_loading import import_string

VARIABLE_KEYS = ("patience", "digital_literacy", "price_sensitivity", "trust_need")
FIXTURE_NAME = "fixture-figma-p07"

# Figma P07 예시 값 (실제 생성값 아님)
FIXTURE_SEGMENTS = [
    {"name": "빠른 구매형", "variables": {"patience": 22, "digital_literacy": 80, "price_sensitivity": 40, "trust_need": 30}},
    {"name": "꼼꼼한 비교형", "variables": {"patience": 70, "digital_literacy": 60, "price_sensitivity": 55, "trust_need": 75}},
    {"name": "가격 민감형", "variables": {"patience": 45, "digital_literacy": 55, "price_sensitivity": 90, "trust_need": 50}},
    {"name": "신중한 첫 방문형", "variables": {"patience": 55, "digital_literacy": 35, "price_sensitivity": 60, "trust_need": 85}},
]


class GenerationError(ValueError):
    pass


def fixture_generate(audience_context, ages, segment_counts):
    return [{"name": s["name"], "variables": dict(s["variables"])} for s in FIXTURE_SEGMENTS[:len(segment_counts)]]


def generate(audience_context, ages, segment_counts):
    path = getattr(settings, "EXITPLAIN_PERSONA_GENERATOR", None)
    name, func = (path, import_string(path)) if path else (FIXTURE_NAME, fixture_generate)
    segments = func(audience_context, ages, segment_counts)
    if not isinstance(segments, list) or len(segments) != len(segment_counts):
        raise GenerationError("세그먼트 수가 요청과 다릅니다.")
    for s in segments:
        if not isinstance(s, dict) or not isinstance(s.get("name"), str) or not s["name"].strip() or len(s["name"]) > 40:
            raise GenerationError("세그먼트 이름이 비었거나 너무 깁니다.")
        v = s.get("variables")
        if (not isinstance(v, dict) or set(v) != set(VARIABLE_KEYS)
                or any(not isinstance(v[k], int) or isinstance(v[k], bool) or not 0 <= v[k] <= 100
                       for k in VARIABLE_KEYS)):
            raise GenerationError("행동 변수는 4개, 0~100 정수여야 합니다.")
    return name, segments