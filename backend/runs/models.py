"""실행(Run) 관련 모델 — 백엔드 A 담당 (명세서 6. 실행, 3.0 진단 기록, 7.3.1 재테스트).

구조:  Run 1 ─ N TaskRun(과업 × 시드 = 에이전트 프로세스 1개) ─ N Session(페르소나 1명) ─ N Observation
       Run 1 ─ N RunEvent  (events.jsonl 한 줄 = 한 행. id 가 폴링 커서)

앱 의존 방향: reports → runs → plans → projects (순환 금지).
에이전트가 내보내는 값(events.jsonl, summary.json)은 agent/runtime.py, agent/schemas.py 를 따른다.
"""
import uuid

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models

from .naming import build_run_name


class Run(models.Model):
    """한 번의 테스트 실행. 확정된 계획(PlanSnapshot)을 그대로 따른다."""

    class Kind(models.TextChoices):
        PRETEST = "pretest", "사전 테스트"
        CAUSE_REPRO = "cause_repro", "원인 재현"
        RETEST = "retest", "재테스트"

    class Status(models.TextChoices):
        QUEUED = "queued", "대기 중"
        RUNNING = "running", "실행 중"
        FINALIZING = "finalizing", "집계 중"
        COMPLETED = "completed", "완료"
        PARTIALLY_COMPLETED = "partially_completed", "일부 완료"
        FAILED = "failed", "실패"
        CANCELLED = "cancelled", "취소됨"

    ACTIVE_STATUSES = (Status.QUEUED, Status.RUNNING, Status.FINALIZING)
    FINAL_STATUSES = (Status.COMPLETED, Status.PARTIALLY_COMPLETED, Status.FAILED, Status.CANCELLED)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="runs")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    sequence_no = models.PositiveIntegerField()  # 프로젝트 안에서 1부터 (진단 기록 번호)
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.PRETEST)
    name = models.CharField(max_length=120, blank=True, default="")  # "4차·첫 주문 완료 외 1개". 비우면 저장할 때 만든다
    parent_run = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children")
    change_note = models.TextField(blank=True, default="")  # 재테스트: 무엇을 고쳤는지

    # 확정 스냅샷이 실행의 모든 입력을 고정한다. 계획·프로젝트가 나중에 바뀌어도 이 Run 은 그대로다.
    plan_snapshot = models.ForeignKey("plans.PlanSnapshot", on_delete=models.PROTECT, related_name="runs")
    config = models.JSONField(default=dict, blank=True)

    status = models.CharField(max_length=24, choices=Status.choices, default=Status.QUEUED)
    idempotency_key = models.CharField(max_length=100, null=True, blank=True)
    cancel_requested_at = models.DateTimeField(null=True, blank=True)
    worker_ref = models.CharField(max_length=100, blank=True, default="")
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=50, blank=True, default="")
    error_message = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["project", "sequence_no"], name="uniq_run_sequence_per_project"),
            models.UniqueConstraint(fields=["project", "idempotency_key"],
                                    condition=models.Q(idempotency_key__isnull=False),
                                    name="uniq_run_idempotency_per_project"),
        ]
        indexes = [models.Index(fields=["project", "-sequence_no"]), models.Index(fields=["status"])]

    def __str__(self):
        return f"Run #{self.sequence_no} ({self.project_id}) {self.status}"

    def save(self, *args, **kwargs):
        if not self.name and self.plan_snapshot_id:
            self.name = build_run_name(self.sequence_no, self.kind, self.plan_snapshot.data)
            if kwargs.get("update_fields") is not None:
                kwargs["update_fields"] = {*kwargs["update_fields"], "name"}
        super().save(*args, **kwargs)

    @property
    def is_active(self):
        return self.status in self.ACTIVE_STATUSES


class TaskRun(models.Model):
    """과업 1개 × 시드 1회 = 에이전트 프로세스 1개 (RunInput 1건)."""

    class Status(models.TextChoices):
        QUEUED = "queued", "대기 중"
        RUNNING = "running", "실행 중"
        SUCCEEDED = "succeeded", "정상 종료"
        FAILED = "failed", "실패"
        CANCELLED = "cancelled", "취소됨"

    run = models.ForeignKey(Run, on_delete=models.CASCADE, related_name="task_runs")
    task_key = models.CharField(max_length=40)  # 과업 id (예: "order")
    seed = models.PositiveSmallIntegerField(validators=[MinValueValidator(1)])  # 1부터
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.QUEUED)
    input_json = models.JSONField(default=dict, blank=True)  # build_run_input 결과 그대로

    # 에이전트 run_id = {run.id}_{task_key}_s{seed}. 결과 폴더: <ARTIFACT_ROOT>/<agent_run_id>/<attempt_id>/
    agent_run_id = models.CharField(max_length=200, blank=True, default="", db_index=True)
    agent_attempt_id = models.CharField(max_length=64, blank=True, default="")
    output_dir = models.CharField(max_length=500, blank=True, default="")
    pid = models.PositiveIntegerField(null=True, blank=True)
    exit_code = models.SmallIntegerField(null=True, blank=True)
    events_offset = models.PositiveBigIntegerField(default=0)  # events.jsonl 을 어디까지 읽었나 (바이트)
    summary = models.JSONField(null=True, blank=True)  # summary.json
    error_code = models.CharField(max_length=50, blank=True, default="")
    error_message = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["run", "task_key", "seed"]
        constraints = [models.UniqueConstraint(fields=["run", "task_key", "seed"], name="uniq_taskrun_task_seed")]

    def __str__(self):
        return f"{self.task_key} s{self.seed} {self.status}"


class Session(models.Model):
    """페르소나 1명의 한 번의 사이트 이용. 값은 session.started / session.finished 이벤트에서 채운다."""

    class Status(models.TextChoices):
        RUNNING = "running", "진행 중"  # 에이전트의 Terminal 상태가 아직 없음
        SUCCESS = "success", "성공"
        SIMULATED_DROPOUT = "simulated_dropout", "이탈"
        AGENT_FAILURE = "agent_failure", "에이전트 실패"
        SITE_FAILURE = "site_failure", "사이트 오류"
        BLOCKED_AUTH = "blocked_auth", "로그인/범위 차단"
        BUDGET_EXCEEDED = "budget_exceeded", "단계 한도 초과"
        CANCELLED = "cancelled", "취소됨"

    # agent.schemas.Terminal 과 같아야 한다 (tests 가 비교한다)
    TERMINAL_STATUSES = (Status.SUCCESS, Status.SIMULATED_DROPOUT, Status.AGENT_FAILURE, Status.SITE_FAILURE,
                         Status.BLOCKED_AUTH, Status.BUDGET_EXCEEDED, Status.CANCELLED)
    # 성공·이탈을 뺀 5개는 에이전트·사이트·환경 문제라 이탈률 분모에서 제외한다.
    # agent.persona_report.TECHNICAL, reports.aggregate 와 같아야 한다 (tests 가 비교한다)
    TECHNICAL_STATUSES = (Status.AGENT_FAILURE, Status.SITE_FAILURE, Status.BLOCKED_AUTH, Status.BUDGET_EXCEEDED,
                          Status.CANCELLED)

    run = models.ForeignKey(Run, on_delete=models.CASCADE, related_name="sessions")  # task_run.run 과 같은 값
    task_run = models.ForeignKey(TaskRun, on_delete=models.CASCADE, related_name="sessions")
    session_key = models.CharField(max_length=100)  # 에이전트의 session_id (예: session_P-001)
    task_id = models.CharField(max_length=40)
    seed = models.PositiveSmallIntegerField()
    persona_snapshot = models.ForeignKey("personas.PersonaSnapshot", on_delete=models.PROTECT, related_name="sessions")

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RUNNING)
    reason = models.TextField(blank=True, default="")
    policy = models.CharField(max_length=60, blank=True, default="")
    model_id = models.CharField(max_length=100, blank=True, default="")
    steps = models.PositiveIntegerField(default=0)
    duration_seconds = models.FloatField(default=0)
    model_calls = models.PositiveIntegerField(default=0)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    terminal_stage = models.CharField(max_length=80, blank=True, default="")  # 마지막으로 도달한 퍼널 단계
    stages_reached = models.JSONField(default=list, blank=True)  # 도달한 퍼널 단계 목록 (session.finished)
    max_frustration = models.FloatField(default=0)  # observation/friction 이벤트의 frustration_score 최댓값
    issues = models.JSONField(default=list, blank=True)  # 에이전트 issue dict 를 그대로 저장
    final_screenshot = models.CharField(max_length=500, blank=True, default="")  # ARTIFACT_ROOT 기준 상대경로

    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["run", "task_id", "seed", "session_key"]
        constraints = [models.UniqueConstraint(fields=["task_run", "session_key"], name="uniq_session_per_taskrun")]
        indexes = [models.Index(fields=["run", "status"])]

    def __str__(self):
        return f"{self.session_key} {self.status}"

    @property
    def is_terminal(self):
        return self.status in self.TERMINAL_STATUSES


class Observation(models.Model):
    """세션이 본 화면 한 장 (observation.created). 스크린샷은 artifact 경로로 서빙한다."""

    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name="observations")
    step = models.PositiveIntegerField()  # step_id "step_003" 의 숫자
    step_id = models.CharField(max_length=20)
    url = models.CharField(max_length=1000)
    artifact = models.CharField(max_length=500, blank=True, default="")  # ARTIFACT_ROOT 기준 상대경로
    required_fields = models.PositiveSmallIntegerField(default=0)
    text = models.TextField(blank=True, default="")  # think-aloud 에 쓰는 화면 텍스트
    stage = models.CharField(max_length=40, blank=True, default="")  # 퍼널 7단계 중 이 화면의 단계
    frustration_score = models.FloatField(null=True, blank=True)
    frustration_threshold = models.FloatField(null=True, blank=True)
    occurred_at = models.DateTimeField()

    class Meta:
        ordering = ["session", "step"]
        constraints = [models.UniqueConstraint(fields=["session", "step"], name="uniq_observation_step")]

    def __str__(self):
        return f"{self.session_id}:{self.step_id}"


class RunEvent(models.Model):
    """events.jsonl 한 줄. 프런트 폴링은 `id > cursor` 로 새 이벤트만 가져간다."""

    # id(BigAuto)가 곧 커서다. 같은 줄을 두 번 가져와도 event_id 가 막아준다.
    run = models.ForeignKey(Run, on_delete=models.CASCADE, related_name="events")
    task_run = models.ForeignKey(TaskRun, on_delete=models.CASCADE, related_name="events")
    session = models.ForeignKey(Session, null=True, blank=True, on_delete=models.CASCADE, related_name="events")
    event_id = models.CharField(max_length=40, unique=True)  # 에이전트가 만든 uuid hex
    sequence = models.PositiveIntegerField()  # 에이전트 프로세스(TaskRun) 안에서 1부터
    type = models.CharField(max_length=40)  # session.started / observation.created / friction.detected / ...
    payload = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        constraints = [models.UniqueConstraint(fields=["task_run", "sequence"], name="uniq_event_sequence_per_taskrun")]
        indexes = [models.Index(fields=["run", "id"])]

    def __str__(self):
        return f"{self.type}#{self.sequence}"
