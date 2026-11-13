"""페르소나 수치 → 이탈 판정 (runtime.py가 쓰는 불만 점수).

점수 Fₜ = max(0, 0.85·Fₜ₋₁ + 입력 부담 + 뒤늦은 비용 + 헤맴 + 되돌아감 − 4·새 단계 도착)
점수가 임계치 이상이면 가상 이탈. 계수는 사람 데이터로 보정하지 않은 가설값이다.

수치(0~100)는 배율 f(x) = 0.5 + x/100 (0 → 0.5배, 50 → 1배, 100 → 1.5배)로 쓴다.
- 인내심(patience) 높음 → 임계치가 높아져 늦게 이탈
- 신뢰 요구(trust_need) 높음 → 필수 입력(개인정보) 부담이 커짐
- 가격 민감도(price_sensitivity) 높음 → 뒤늦게 나온 배송비·수수료에 크게 반응
- 디지털 숙련도(digital_literacy) 낮음 → 헤맬 때(스크롤·뒤로가기·되돌아감) 더 답답해함
모든 수치가 50(기본값)이고 seed가 0이면 예전 규칙(필수 입력 초과 시 +70, 임계치 60)과 같은 결과가 나온다.

seed: 0이면 흔들림 없음. 1 이상이면 (seed, 페르소나 ID)로 정해지는 난수로 임계치와 가중치를
±15% 흔든다. 같은 seed면 항상 같은 결과, seed가 다르면 경계에 있는 페르소나의 결과가 달라진다.
"""
import random
import re

DECAY = 0.85
WEIGHTS = {"input_burden": 70.0, "cost": 40.0, "navigation": 10.0, "revisit": 8.0}
PROGRESS_REWARD = 4.0
JITTER = 0.15
EARLY_STAGES = {"landing", "browse", "detail"}
LATE_STAGES = {"cart", "signup", "payment"}
WANDER_ACTIONS = {"scroll", "back", "wait", "reload"}
COST_TEXT = re.compile(r"(배송비|수수료|추가\s*요금|추가\s*비용)[^.\n]{0,20}?\d[\d,]*\s*원")


def factor(value):
    return 0.5 + value / 100


class PersonaMeter:
    def __init__(self, persona, seed=0):
        rng = random.Random(f"{seed}:{persona.id}")
        jitter = (lambda: 1.0) if seed == 0 else (lambda: rng.uniform(1 - JITTER, 1 + JITTER))
        self.threshold = round(persona.frustration_threshold * factor(persona.patience) * jitter(), 3)
        self.required_limit = persona.max_required_fields
        wander = factor(100 - persona.digital_literacy)
        self.weights = {
            "input_burden": WEIGHTS["input_burden"] * factor(persona.trust_need) * jitter(),
            "cost": WEIGHTS["cost"] * factor(persona.price_sensitivity) * jitter(),
            "navigation": WEIGHTS["navigation"] * wander * jitter(),
            "revisit": WEIGHTS["revisit"] * wander * jitter(),
        }
        self.score = 0.0
        self.contributions = {"input_burden": 0.0, "cost": 0.0, "navigation": 0.0}
        self.stages = []
        self.previous_stage = None
        self.fired = set()
        self.cost_known = False

    def observe(self, stage, required_fields, text="", cost_boxes=(), last_action=None):
        """한 화면을 본 결과. signals에 이번에 생긴 마찰과 점수 기여가 들어간다."""
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