"""페르소나 수치 → 이탈 판정 (runtime.py가 쓰는 불만 점수).

점수 Fₜ = max(0, 0.85·Fₜ₋₁ + (아래 7가지 원인의 이번 화면 기여) − 4·새 단계 도착)
점수가 임계치 이상이면 가상 이탈. 계수는 사람 데이터로 보정하지 않은 가설값이다.

수치(0~100)는 배율 f(x) = 0.5 + x/100 (0 → 0.5배, 50 → 1배, 100 → 1.5배)로 쓴다.
- 인내심(patience) 높음 → 임계치가 높아져 늦게 이탈
- 신뢰 요구(trust_need) 높음 → 필수 입력(개인정보) 부담이 커짐
- 가격 민감도(price_sensitivity) 높음 → 뒤늦게 나온 배송비·수수료에 크게 반응
- 디지털 숙련도(digital_literacy) 낮음 → 헤맬 때(스크롤·뒤로가기·되돌아감) 더 답답해함

이탈 원인 7종 (리포트 P12, FE causeNames와 같은 키·이름)
- input_burden 입력 부담: 필수 입력칸이 페르소나 허용치보다 많음 (신뢰 요구 배율)
- navigation 탐색 실패: 같은 단계에서 스크롤·뒤로가기 반복, 앞 단계로 되돌아감 (숙련도 낮을수록)
- cost 비용 충격: 앞 단계에서 안 보이던 배송비·수수료가 장바구니 이후에 나타남 (가격 민감도)
- value 가치 불명확: 상세 화면에 가격이 보이지 않음 (가격 민감도)
- trust 신뢰 부족: 결제 전 단계에서 연락처·생년월일 같은 개인정보를 먼저 요구함 (신뢰 요구)
- technical 기술 오류·지연: 행동 후 화면이 3초 넘게 걸리거나 오류 문구가 보임 (인내심 낮을수록)
- accessibility 접근성 장벽: 이름 없는 입력칸, 모바일에서 24px보다 작은 버튼이 2개 이상 (숙련도 낮을수록)
모든 수치가 50(기본값)이고 seed가 0이면 예전 규칙(필수 입력 초과 시 +70, 임계치 60)과 같은 결과가 나온다.

seed: 0이면 흔들림 없음. 1 이상이면 (seed, 페르소나 ID)로 정해지는 난수로
1) 수치 4개를 원래 값 중심으로 다시 뽑고 (표준편차 12, 같은 세그먼트 안의 개인차)
2) 임계치와 가중치를 ±15% 흔든다.
같은 seed면 항상 같은 결과, seed가 다르면 경계에 있는 페르소나의 결과가 달라진다.
문제가 아주 크면(예: 필수 입력이 허용치를 크게 넘음) 시드가 달라도 모두 이탈할 수 있다. 이건 정상이다.
"""
import random
import re

DECAY = 0.85
CAUSES = {"input_burden": "입력 부담", "navigation": "탐색 실패", "cost": "비용 충격", "value": "가치 불명확",
          "trust": "신뢰 부족", "technical": "기술 오류·지연", "accessibility": "접근성 장벽"}
WEIGHTS = {"input_burden": 70.0, "cost": 40.0, "navigation": 10.0, "revisit": 8.0,
           "value": 20.0, "trust": 30.0, "technical": 15.0, "accessibility": 20.0}
PROGRESS_REWARD = 4.0
JITTER = 0.15
VARIABLE_SD = 12
VARIABLE_KEYS = ("patience", "digital_literacy", "price_sensitivity", "trust_need")
EARLY_STAGES = {"landing", "browse", "detail"}
LATE_STAGES = {"cart", "signup", "payment"}
WANDER_ACTIONS = {"scroll", "back", "wait", "reload"}
COST_TEXT = re.compile(r"(배송비|수수료|추가\s*요금|추가\s*비용)[^.\n]{0,20}?\d[\d,]*\s*원")
PRICE_TEXT = re.compile(r"\d[\d,]*\s*원")
ERROR_TEXT = re.compile(r"(오류가 발생|에러가 발생|일시적인 (문제|오류)|잠시 후 다시 시도|페이지를 찾을 수 없|서버 오류|Internal Server Error|Not Found)")
SENSITIVE_WORDS = ("연락처", "전화", "휴대폰", "핸드폰", "생년월일", "주민", "카드", "계좌", "주소")
SLOW_SECONDS = 3.0
SMALL_TARGET_PX = 24


def factor(value):
    return 0.5 + value / 100

def sample_persona(persona, rng):
    """같은 세그먼트 안에서도 사람마다 조금씩 다르다: 수치 4개를 평균 그대로, 표준편차 VARIABLE_SD로 뽑는다."""
    def draw(value):
        return min(100, max(0, round(rng.gauss(value, VARIABLE_SD))))
    return persona.model_copy(update={k: draw(getattr(persona, k)) for k in VARIABLE_KEYS})


class PersonaMeter:
    def __init__(self, persona, seed=0):
        rng = random.Random(f"{seed}:{persona.id}")
        jitter = (lambda: 1.0) if seed == 0 else (lambda: rng.uniform(1 - JITTER, 1 + JITTER))
        if seed:
            persona = sample_persona(persona, rng)
        self.variables = {k: getattr(persona, k) for k in VARIABLE_KEYS}   # 이번 시드에서 실제로 쓴 수치

        self.threshold = round(persona.frustration_threshold * factor(persona.patience) * jitter(), 3)
        self.required_limit = persona.max_required_fields
        wander = factor(100 - persona.digital_literacy)
        self.weights = {
            "input_burden": WEIGHTS["input_burden"] * factor(persona.trust_need) * jitter(),
            "cost": WEIGHTS["cost"] * factor(persona.price_sensitivity) * jitter(),
            "navigation": WEIGHTS["navigation"] * wander * jitter(),
            "revisit": WEIGHTS["revisit"] * wander * jitter(),
            "value": WEIGHTS["value"] * factor(persona.price_sensitivity) * jitter(),
            "trust": WEIGHTS["trust"] * factor(persona.trust_need) * jitter(),
            "technical": WEIGHTS["technical"] * factor(100 - persona.patience) * jitter(),
            "accessibility": WEIGHTS["accessibility"] * wander * jitter(),
        }
        self.score = 0.0
        self.contributions = {key: 0.0 for key in CAUSES}
        self.stages = []
        self.previous_stage = None
        self.fired = set()
        self.cost_known = False

    def observe(self, stage, required_fields, text="", cost_boxes=(), last_action=None,
                elements=(), viewport_width=1000, wait_seconds=0.0):
        """한 화면을 본 결과. signals에 이번에 생긴 마찰과 점수 기여가 들어간다.
        elements: browser.observe가 준 화면 요소 목록, wait_seconds: 직전 행동 후 화면이 나오기까지 걸린 시간"""
        new_stage = stage not in self.stages
        if new_stage:
            self.stages.append(stage)
        signals = {}
        if required_fields > self.required_limit and "input_burden" not in self.fired:
            signals["input_burden"] = self.weights["input_burden"]
        cost_visible = bool(cost_boxes) or bool(COST_TEXT.search(text or ""))
        if cost_visible and stage in EARLY_STAGES:
            self.cost_known = True      # 앞 단계에서 미리 보여 줬으면 뒤늦은 비용이 아님
        elif cost_visible and stage in LATE_STAGES and not self.cost_known and "cost" not in self.fired:
            signals["cost"] = self.weights["cost"]
        if not new_stage and last_action in WANDER_ACTIONS:
            signals["navigation"] = self.weights["navigation"]
        if not new_stage and self.previous_stage is not None and self.previous_stage != stage:
            signals["navigation"] = signals.get("navigation", 0) + self.weights["revisit"]
        if stage == "detail" and not PRICE_TEXT.search(text or "") and "value" not in self.fired:
            signals["value"] = self.weights["value"]
        if stage not in {"payment", "complete"} and "trust" not in self.fired and _asks_sensitive(elements):
            signals["trust"] = self.weights["trust"]
        if "technical" not in self.fired and (wait_seconds > SLOW_SECONDS or ERROR_TEXT.search(text or "")):
            signals["technical"] = self.weights["technical"]
        if "accessibility" not in self.fired and _barriers(elements, viewport_width) >= 2:
            signals["accessibility"] = self.weights["accessibility"]
        self.fired.update(k for k in signals if k != "navigation")
        for key, value in signals.items():
            self.contributions[key] += value
        previous = self.score
        self.score = round(max(0.0, DECAY * previous + sum(signals.values()) - PROGRESS_REWARD * new_stage), 3)
        self.previous_stage = stage
        return {"stage": stage, "score": self.score, "previous_score": previous, "threshold": self.threshold,
                "signals": {k: round(v, 3) for k, v in signals.items()}, "crossed": self.score >= self.threshold}

    def primary_cause(self):
        """지금까지 점수에 가장 많이 기여한 원인 (없으면 None)."""
        key, value = max(self.contributions.items(), key=lambda item: item[1])
        return key if value > 0 else None


def _is_field(element):
    return element.get("tag") in {"input", "select", "textarea"} and element.get("type") not in {"hidden", "submit", "button"}


def _asks_sensitive(elements):
    for element in elements:
        if not _is_field(element):
            continue
        words = f"{element.get('label', '')} {element.get('text', '')}"
        if element.get("type") == "tel" or any(w in words for w in SENSITIVE_WORDS):
            return True
    return False


def _barriers(elements, viewport_width):
    count = 0
    for element in elements:
        if _is_field(element) and element.get("type") not in {"checkbox", "radio"} \
                and not (element.get("label") or element.get("text")):
            count += 1          # 무엇을 입력하는 칸인지 이름이 없음
        box = element.get("box") or {}
        if viewport_width <= 480 and element.get("tag") in {"a", "button"} and box \
                and 0 < max(box.get("width", 0), box.get("height", 0)) < SMALL_TARGET_PX:
            count += 1          # 모바일에서 누르기 어려운 작은 버튼
    return count