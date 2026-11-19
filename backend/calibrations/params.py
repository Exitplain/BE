"""보정 값 검사·적용 (CAL-02·03). 실측 숫자가 아니라 배율만 다룬다.

페르소나 한 명의 배율 = "*"(전체) 값 × 그 세그먼트 값. 둘 다 없으면 1.0 (기본 모형).
"""
from agent.behavior import CAUSES

THRESHOLD_RANGE = (0.5, 2.0)
WEIGHT_RANGE = (0.25, 3.0)
BASE_THRESHOLD = 60.0
STEP = 0.05


class ParamsError(ValueError):
    def __init__(self, fields):
        self.fields = fields
        super().__init__("예측 맞추기 값이 올바르지 않습니다.")


def snap(value):
    """0.05 단위로 맞춘다."""
    return round(round(float(value) / STEP) * STEP, 2)


def validate(params):
    """모르는 키·원인, 범위 밖 값을 거부하고 0.05 단위로 맞춘 사본을 돌려준다."""
    errors = {}
    if not isinstance(params, dict) or set(params) - {"segments"}:
        raise ParamsError({"params": ["{\"segments\": {...}} 형식이어야 합니다."]})
    segments = params.get("segments", {})
    if not isinstance(segments, dict):
        raise ParamsError({"params.segments": ["객체여야 합니다."]})
    clean = {}
    for sid, values in segments.items():
        where = f"params.segments.{sid}"
        if not isinstance(sid, str) or not sid or len(sid) > 40 or not isinstance(values, dict) \
                or set(values) - {"threshold_scale", "weight_scale"}:
            errors[where] = ["세그먼트 값 형식이 올바르지 않습니다."]
            continue
        out = {}
        if "threshold_scale" in values:
            value = values["threshold_scale"]
            if isinstance(value, bool) or not isinstance(value, (int, float)) \
                    or not THRESHOLD_RANGE[0] <= value <= THRESHOLD_RANGE[1]:
                errors[f"{where}.threshold_scale"] = [f"{THRESHOLD_RANGE[0]}~{THRESHOLD_RANGE[1]} 이어야 합니다."]
            else:
                out["threshold_scale"] = snap(value)
        weights = values.get("weight_scale") or {}
        if not isinstance(weights, dict):
            errors[f"{where}.weight_scale"] = ["객체여야 합니다."]
            weights = {}
        clean_weights = {}
        for cause, value in weights.items():
            if cause not in CAUSES:
                errors[f"{where}.weight_scale.{cause}"] = ["모르는 원인입니다."]
            elif isinstance(value, bool) or not isinstance(value, (int, float)) \
                    or not WEIGHT_RANGE[0] <= value <= WEIGHT_RANGE[1]:
                errors[f"{where}.weight_scale.{cause}"] = [f"{WEIGHT_RANGE[0]}~{WEIGHT_RANGE[1]} 이어야 합니다."]
            else:
                clean_weights[cause] = snap(value)
        if clean_weights:
            out["weight_scale"] = clean_weights
        if out:
            clean[sid] = out
    if errors:
        raise ParamsError(errors)
    return {"segments": clean}


def for_segment(params, segment_id):
    """한 세그먼트에 적용할 (threshold_scale, weight_scale). 전체("*") × 세그먼트, 범위로 자른다."""
    segments = (params or {}).get("segments") or {}
    every, own = segments.get("*") or {}, segments.get(segment_id) or {}
    threshold = (every.get("threshold_scale", 1.0)) * (own.get("threshold_scale", 1.0))
    threshold = min(THRESHOLD_RANGE[1], max(THRESHOLD_RANGE[0], threshold))
    weights = {}
    for cause in set(every.get("weight_scale") or {}) | set(own.get("weight_scale") or {}):
        value = (every.get("weight_scale") or {}).get(cause, 1.0) * (own.get("weight_scale") or {}).get(cause, 1.0)
        value = min(WEIGHT_RANGE[1], max(WEIGHT_RANGE[0], value))
        if value != 1.0:
            weights[cause] = round(value, 4)
    return round(threshold, 4), weights


def apply_to_persona(persona, params, segment_id):
    """에이전트 페르소나 dict 에 보정을 반영한다 (CAL-03). 보정이 없으면 그대로 둔다."""
    threshold, weights = for_segment(params, segment_id)
    if threshold != 1.0:
        persona["frustration_threshold"] = round(BASE_THRESHOLD * threshold, 3)
    if weights:
        persona["weight_scale"] = weights
    return persona
