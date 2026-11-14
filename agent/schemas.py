from typing import Literal
from urllib.parse import urlsplit
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .funnel import FUNNEL_KEYS


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Persona(StrictModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    context: str = "처음 방문한 사용자"
    max_required_fields: int = Field(default=6, ge=0, le=100)
    frustration_threshold: float = Field(default=60, gt=0)
    # 페르소나 수치 4개 (0~100, 위저드 P07). 없으면 50(보통)으로 본다.
    patience: int = Field(default=50, ge=0, le=100)            # 인내심: 높을수록 늦게 이탈
    digital_literacy: int = Field(default=50, ge=0, le=100)    # 디지털 숙련도: 낮을수록 헤맬 때 더 답답해함
    price_sensitivity: int = Field(default=50, ge=0, le=100)   # 가격 민감도: 높을수록 뒤늦은 추가 비용에 민감
    trust_need: int = Field(default=50, ge=0, le=100)          # 신뢰 요구: 높을수록 개인정보 입력을 부담스러워함


class RunInput(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    run_id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    target_url: str
    allowed_origins: list[str]
    goal: str
    success_path: str
    personas: list[Persona] = Field(min_length=1, max_length=20)
    max_steps: int = Field(default=20, ge=1, le=100)
    timeout_seconds: float = Field(default=120, gt=0, le=600)
    concurrency: int = Field(default=2, ge=1, le=5)    # 동시 실행 (위저드 기본 5)
    # 세션 하나가 쓸 수 있는 모델 토큰(입력+출력) 상한. 넘으면 budget_exceeded로 멈춘다. None이면 제한 없음
    max_tokens_per_session: int | None = Field(default=None, ge=1)
    # 시드: 같은 시드면 같은 결과, 시드가 다르면 페르소나 반응이 조금씩 달라진다 (리포트의 시드 범위 표시용)
    seed: int = Field(default=0, ge=0, le=1_000_000)
    # 브라우저 화면 크기 (모바일 390). 기존과 같게 기본값은 1000×800
    viewport_width: int = Field(default=1000, ge=320, le=1920)
    viewport_height: int = Field(default=800, ge=480, le=1600)
    # 행동 사이 대기 시간(초). 대상 사이트에 요청이 몰리지 않게 (위저드 '요청 간격 1초')
    action_interval_seconds: float = Field(default=0, ge=0, le=5)
    # 사이트 분석으로 정한 '경로 → 퍼널 단계' 표 (예: {"/goods": "detail"}). 없으면 경로 단어로 추측
    stage_paths: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_run(self):
        def origin(url):
            p = urlsplit(url)
            if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
                raise ValueError("Only HTTP(S) URLs without credentials are supported")
            return f"{p.scheme}://{p.netloc}"
        origins = [origin(x) for x in self.allowed_origins]
        if origins != self.allowed_origins or origin(self.target_url) not in origins:
            raise ValueError("Use exact origins and include the target origin")
        if not self.success_path.startswith("/"):
            raise ValueError("success_path must start with /")
        if len({p.id for p in self.personas}) != len(self.personas):
            raise ValueError("Persona IDs must be unique")
        for path, stage in self.stage_paths.items():
            if not path.startswith("/") or stage not in FUNNEL_KEYS:
                raise ValueError("stage_paths must map /paths to funnel stages")
        return self


class Action(StrictModel):
    kind: Literal["click", "fill", "scroll", "hover", "select", "check", "uncheck", "press", "wait", "back", "forward", "reload", "stop"]
    target_id: str | None
    value: str | None
    explanation: str


Terminal = Literal["success", "simulated_dropout", "agent_failure", "site_failure", "blocked_auth", "budget_exceeded", "cancelled"]


class SessionResult(StrictModel):
    session_id: str
    persona_id: str
    policy: str
    model_id: str | None
    status: Terminal
    reason: str
    steps: int
    duration_seconds: float
    model_calls: int
    input_tokens: int
    output_tokens: int
    issues: list[dict]
    final_artifact: str | None
    terminal_stage: str | None = None                          # 마지막으로 본 화면의 퍼널 단계
    stages_reached: list[str] = Field(default_factory=list)    # 처음 도달한 순서대로