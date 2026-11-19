"""세션이 왜 끝났는지 사람이 읽는 한 줄 (실행 중 피드·세션 상세·리포트에 같은 문장).

가상 이탈이면 '어느 화면에서 · 주 원인과 근거 · 불만 점수/임계치 · 함께 쌓인 원인 · 직전 행동 설명'을 붙인다.
근거는 runtime.py 가 이탈 순간 주 원인 issue 에 남긴 dropout 기록이다 (예전 세션은 issue 만으로 줄여서 만든다).
점수는 PersonaMeter 의 가설 모델 값이고, 사람의 실제 이유가 아니라 '이 모델이 이탈로 판정한 근거'다.
"""
from .behavior import CAUSES
from .funnel import stage_name

DETAILS = {
    "input_burden": "필수 입력칸이 이 사람이 견딜 만한 수보다 많음",
    "cost": "장바구니 이후에 처음 보인 배송비·수수료",
    "navigation": "같은 단계에서 스크롤·뒤로가기를 반복하거나 앞 화면으로 되돌아감",
    "value": "상세 화면에 가격이 보이지 않음",
    "trust": "담기 전에 연락처·생년월일 같은 개인정보를 먼저 요구함",
    "technical": "화면이 3초 넘게 걸리거나 오류 문구가 보임",
    "accessibility": "이름 없는 입력칸이나 너무 작은 버튼",
}

ENDINGS = {
    "success": "목표 화면에 도달했어요",
    "agent_failure": "에이전트가 더 진행할 방법을 찾지 못해 멈췄어요 (이탈이 아니라 기술 실패)",
    "site_failure": "사이트가 오류로 응답해 멈췄어요",
    "blocked_auth": "로그인에 실패했거나 허용한 사이트 밖으로 나가 멈췄어요",
    "budget_exceeded": "시간·토큰 한도에 걸려 멈췄어요",
    "cancelled": "실행을 중지해서 멈췄어요",
}


def _detail(issue):
    key = issue.get("category")
    if key == "input_burden" and issue.get("observed_fields") is not None and issue.get("configured_limit") is not None:
        return f"필수 입력 {issue['observed_fields']}칸 (허용 {issue['configured_limit']}칸)"
    return DETAILS.get(key, "")


def _where(dropout, fallback_stage):
    stage = (dropout or {}).get("stage") or fallback_stage
    path = (dropout or {}).get("path")
    name = stage_name(stage) if stage else ""
    if name and path:
        return f"{name} 화면({path})"
    return f"{name} 화면" if name else (f"{path} 화면" if path else "")


def describe(status, reason="", issues=(), terminal_stage=None):
    """세션 결과 → 한 줄 설명. issues 는 SessionResult.issues (dict 목록)."""
    issues = [i for i in issues or () if isinstance(i, dict)]
    if status != "simulated_dropout":
        text = ENDINGS.get(status) or (reason or "")
        if status == "site_failure" and reason:
            text += f" ({reason})"
        return text
    primary = next((i for i in issues if i.get("primary")), None)
    dropout = (primary or {}).get("dropout") or {}
    where = _where(dropout, terminal_stage)
    parts = [f"{where}에서 이탈" if where else "이탈"]
    if primary:
        name = CAUSES.get(primary.get("category"), primary.get("category"))
        detail = _detail(primary)
        parts.append(f"주 원인 {name}" + (f": {detail}" if detail else ""))
    else:
        parts.append("주 원인 미상")
    if dropout.get("score") is not None and dropout.get("threshold") is not None:
        parts.append(f"불만 {dropout['score']:.0f}/{dropout['threshold']:.0f}")
    others = [CAUSES.get(i.get("category"), i.get("category")) for i in issues
              if i is not primary and i.get("category")]
    if others:
        parts.append("함께 쌓인 원인 " + ", ".join(others))
    if dropout.get("last_thought"):
        parts.append(f"직전 행동: {dropout['last_thought']}")
    return " · ".join(parts)
