"""세션이 왜 끝났는지 사람이 말하듯 쓴 짧은 글 (실행 중 피드·세션 상세·리포트에 같은 문장).

가상 이탈이면 그 페르소나가 직접 말하는 말투로 '어디서 그만뒀는지 → 왜(주 원인과 그 근거 숫자) →
앞에서 쌓인 불편 → 직전에 하던 일'을 쓴다. 점수·임계치 같은 내부 값은 문장에 넣지 않는다
(주 원인 issue 의 dropout 기록에 그대로 있다).
근거는 runtime.py 가 이탈 순간 주 원인 issue 에 남긴 dropout 기록이다 (예전 세션은 issue 만으로 줄여서 만든다).
말투만 사람처럼 바꾼 것이고, 실제 사람의 답이 아니라 이탈 모델(PersonaMeter)이 판정한 근거다.
"""
from .funnel import stage_name

# 주 원인: 그만둔 이유를 페르소나가 말하듯
REASONS = {
    "input_burden": "입력할 칸이 너무 많아서 부담스러웠어요.",
    "cost": "장바구니에 담고 나서야 배송비나 수수료가 보여서, 생각보다 비싸다고 느꼈어요.",
    "navigation": "원하는 걸 찾으려고 여기저기 왔다 갔다 했는데, 어디로 가야 할지 잘 모르겠더라고요.",
    "value": "상품 화면에 가격이 안 보여서 살지 말지 판단이 안 됐어요.",
    "trust": "아직 살지도 모르는데 연락처 같은 개인정보부터 물어봐서 꺼려졌어요.",
    "technical": "화면이 늦게 뜨거나 오류가 나서 답답했어요.",
    "accessibility": "뭘 입력하라는 건지 알아보기 어렵거나 버튼이 너무 작아서 누르기 힘들었어요.",
}
# 함께 쌓인 원인: '앞에서도 ○○ 때문에 조금씩 지쳐 있었어요'
EARLIER = {
    "input_burden": "입력할 게 많은 것",
    "cost": "뒤늦게 보인 추가 비용",
    "navigation": "길을 헤맨 것",
    "value": "가격이 안 보인 것",
    "trust": "개인정보를 먼저 묻는 것",
    "technical": "느린 화면이나 오류",
    "accessibility": "알아보기·누르기 어려운 화면",
}

ENDINGS = {
    "success": "끝까지 진행해서 목표 화면에 도착했어요.",
    "agent_failure": "이탈은 아니에요. 에이전트가 더 진행할 방법을 찾지 못해 멈췄어요 (기술 실패).",
    "site_failure": "이탈은 아니에요. 사이트가 오류로 응답해서 멈췄어요.",
    "blocked_auth": "이탈은 아니에요. 로그인에 실패했거나 허용한 사이트 밖으로 나가서 멈췄어요.",
    "budget_exceeded": "이탈은 아니에요. 시간·토큰 한도에 걸려서 멈췄어요.",
    "cancelled": "실행을 중지해서 멈췄어요.",
}


def _reason(issue):
    key = issue.get("category")
    if key == "input_burden" and issue.get("observed_fields") is not None:
        text = f"입력할 칸이 {issue['observed_fields']}개나 돼서 부담스러웠어요."
        if issue.get("configured_limit") is not None:
            text += f" {issue['configured_limit']}개 정도까지는 괜찮았을 거예요."
        return text
    return REASONS.get(key, "")


def _where(dropout, fallback_stage):
    stage = (dropout or {}).get("stage") or fallback_stage
    name = stage_name(stage) if stage else ""
    if name and not name.startswith("/"):
        return f"{name} 화면"
    path = (dropout or {}).get("path") or name
    return f"{path} 화면" if path else ""


def describe(status, reason="", issues=(), terminal_stage=None):
    """세션 결과 → 사람이 말하듯 쓴 1~4문장. issues 는 SessionResult.issues (dict 목록)."""
    issues = [i for i in issues or () if isinstance(i, dict)]
    if status != "simulated_dropout":
        text = ENDINGS.get(status) or (reason or "")
        if status == "site_failure" and reason:
            text = text.rstrip(".") + f" ({reason})."
        return text
    primary = next((i for i in issues if i.get("primary")), None)
    dropout = (primary or {}).get("dropout") or {}
    where = _where(dropout, terminal_stage)
    sentences = [f"{where}에서 그만뒀어요." if where else "여기서 그만뒀어요."]
    reason_text = _reason(primary) if primary else ""
    sentences.append(reason_text or "왜 그만뒀는지는 기록이 남지 않았어요.")
    earlier = [EARLIER[i["category"]] for i in issues
               if i is not primary and i.get("category") in EARLIER]
    if earlier:
        sentences.append(f"앞에서도 {', '.join(earlier)} 때문에 조금씩 지쳐 있었어요.")
    if dropout.get("last_thought"):
        sentences.append(f"그때는 '{dropout['last_thought']}' 하려던 참이었어요.")
    return " ".join(sentences)
