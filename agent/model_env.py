"""Claude 모델 이름의 기본값과 환경변수 규칙. 모델을 바꿀 때 코드를 고치지 않고 서버 .env 로 정한다.

우선순위(앞이 이긴다): 용도별 환경변수 → DROPSPOT_DEFAULT_MODEL(전체 기본) → DEFAULT_MODEL.
값이 비어 있으면(예: `DROPSPOT_JUDGE_MODEL=`) 없는 것으로 보고 다음 순서로 넘어간다.

용도별 환경변수
- DROPSPOT_CLAUDE_MODEL       에이전트가 평소 스텝을 고르는 모델 (claude 정책, AGENT_MODEL 이 있으면 그 값이 우선)
- DROPSPOT_ESCALATION_MODEL   에이전트가 막혔을 때 다시 묻는 모델
- DROPSPOT_JUDGE_MODEL        성공 기준 판정 모델 (agent/judge.py)
- DROPSPOT_EXPLORE_MODEL      처음 보는 URL 둘러보기·과업 제안 모델 (agent/explore.py)
- DROPSPOT_CRITERIA_MODEL     직접 추가한 과업의 성공 기준 다듬기 (없으면 DROPSPOT_EXPLORE_MODEL 을 따른다)
백엔드 쪽: EXITPLAIN_PERSONA_MODEL(페르소나 생성), EXITPLAIN_STAGE_NAMER_MODEL(화면 역할 이름)도 같은 규칙이다.
"""
import os

DEFAULT_MODEL = "claude-haiku-4-5"
GLOBAL_ENV = "DROPSPOT_DEFAULT_MODEL"


def model_from_env(*names):
    """names 순서대로 비어 있지 않은 환경변수를 찾고, 없으면 DROPSPOT_DEFAULT_MODEL, 그것도 없으면 DEFAULT_MODEL."""
    for name in (*names, GLOBAL_ENV):
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return DEFAULT_MODEL
