from typing import Literal
from urllib.parse import urlsplit
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Persona(StrictModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    context: str = "처음 방문한 사용자"
    max_required_fields: int = Field(default=6, ge=0, le=100)
    frustration_threshold: float = Field(default=60, gt=0)


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
    concurrency: int = Field(default=2, ge=1, le=4)

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
