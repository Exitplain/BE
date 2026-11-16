"""규칙 기반 페르소나 생성기 (LLM 없이 동작하는 기본 생성기).

generate(audience_context, ages, segment_counts) -> [{"name", "variables": {patience, digital_literacy,
price_sensitivity, trust_need}, "basis": [...]}]

방법
1) Figma P07의 세그먼트 4개(빠른 구매형·꼼꼼한 비교형·가격 민감형·신중한 첫 방문형)를 출발점으로 쓴다.
2) 연령대와 타겟 설명에 들어 있는 단어로 수치를 더하고 뺀다 (아래 AGE_SHIFT, KEYWORD_SHIFT).
   연령대를 여러 개 고르면 평균을 쓰고, 같은 규칙의 단어가 여러 번 나와도 한 번만 반영한다.
3) 5~95 사이로 자른다 (0·100 같은 극단값은 만들지 않음).
같은 입력이면 항상 같은 결과다. 숫자는 사람 데이터로 보정하지 않은 가설값이다.
LLM 생성기가 생기면 settings.EXITPLAIN_PERSONA_GENERATOR로 바꿔 끼우면 된다.
"""
BASE_SEGMENTS = [
    ("빠른 구매형", {"patience": 22, "digital_literacy": 80, "price_sensitivity": 40, "trust_need": 30}),
    ("꼼꼼한 비교형", {"patience": 70, "digital_literacy": 60, "price_sensitivity": 55, "trust_need": 75}),
    ("가격 민감형", {"patience": 45, "digital_literacy": 55, "price_sensitivity": 90, "trust_need": 50}),
    ("신중한 첫 방문형", {"patience": 55, "digital_literacy": 35, "price_sensitivity": 60, "trust_need": 85}),
]

AGE_SHIFT = {
    "20s": {"digital_literacy": 12, "patience": -8},
    "30s": {"digital_literacy": 5, "patience": -3},
    "40s": {"digital_literacy": -5, "patience": 3},
    "50plus": {"digital_literacy": -18, "patience": 8, "trust_need": 10},
}

# (설명, 단어들, 바뀌는 수치)
KEYWORD_SHIFT = [
    ("처음 방문", ("처음", "첫 ", "첫주문", "첫 주문", "신규"), {"trust_need": 10, "digital_literacy": -3}),
    ("시간 부족", ("바쁜", "바쁘", "퇴근", "출근", "점심", "급하", "빠르게", "빨리"), {"patience": -10}),
    ("가격 중시", ("가격", "할인", "저렴", "쿠폰", "알뜰", "가성비", "최저가", "학생"), {"price_sensitivity": 12}),
    ("품질 중시", ("프리미엄", "고급", "유기농", "명품", "품질"), {"price_sensitivity": -10, "trust_need": 5}),
    ("모바일 사용", ("모바일", "스마트폰", "앱", "휴대폰"), {"digital_literacy": 5}),
    ("디지털 미숙", ("시니어", "어르신", "부모님", "노년", "디지털이 익숙하지", "서툰"),
     {"digital_literacy": -15, "patience": 5, "trust_need": 8}),
    ("신중한 비교", ("꼼꼼", "비교", "신중", "리뷰", "후기"), {"patience": 10, "trust_need": 5}),
]
LOW, HIGH = 5, 95
NAME = "rules-v1"


def _clamp(value):
    return max(LOW, min(HIGH, round(value)))


def shifts(audience_context, ages):
    """적용할 변화량과 그 근거(사람이 읽을 수 있는 설명)."""
    total, basis = {}, []
    picked = [a for a in ages if a in AGE_SHIFT]
    for age in picked:
        for key, value in AGE_SHIFT[age].items():
            total[key] = total.get(key, 0) + value / len(picked)
    if picked:
        basis.append("연령: " + ", ".join(picked))
    text = audience_context or ""
    for label, words, change in KEYWORD_SHIFT:
        hits = [w.strip() for w in words if w in text]
        if hits:
            for key, value in change.items():
                total[key] = total.get(key, 0) + value
            basis.append(f"{label}: '{hits[0]}'")
    return total, basis


def generate(audience_context, ages, segment_counts):
    total, basis = shifts(audience_context, ages)
    result = []
    for name, base in BASE_SEGMENTS[:len(segment_counts)]:
        variables = {key: _clamp(value + total.get(key, 0)) for key, value in base.items()}
        result.append({"name": name, "variables": variables, "basis": list(basis)})
    return result