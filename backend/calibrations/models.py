from django.conf import settings
from django.db import models


class CalibrationRevision(models.Model):
    """프로젝트의 보정 값 한 판 (CAL-02, docs/실측_보정_요구사항.md). 만든 뒤에는 바꾸지 않는다.

    가장 큰 revision 이 현재 보정이다. 되돌리기도 이전 값을 복사한 새 revision 으로 남긴다 (CAL-14).
    params: {"segments": {segment_id 또는 "*": {"threshold_scale": 0.5~2.0, "weight_scale": {원인: 0.25~3.0}}}}
    """
    project = models.ForeignKey(settings.EXITPLAIN_PROJECT_MODEL, on_delete=models.CASCADE,
                                related_name="calibration_revisions")
    revision = models.PositiveIntegerField()
    params = models.JSONField(default=dict)
    changes = models.JSONField(default=list, blank=True)     # 이번에 바꾼 행 (현재→제안, 근거)
    basis = models.JSONField(default=dict, blank=True)       # 기준 비교 (리포트·차수·기간·평균 오차)
    restored_from = models.PositiveIntegerField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-revision"]
        constraints = [models.UniqueConstraint(fields=["project", "revision"], name="uniq_calibration_revision")]


class HumanObservation(models.Model):
    """사람 관측 1건 (K04, CAL-16~18). 실제 참여자가 과업을 해 본 결과를 진행자가 넣는다. 가짜 사람 데이터는 넣지 않는다.

    같은 프로젝트·참여자·과업은 1건이다 (다시 넣으면 덮어쓴다). 이름·연락처 같은 개인정보 칸은 없다.
    purpose: calibration(보정 근거로 쓸 수 있음) / evaluation(평가용: 조정안 계산에 쓰지 않음)
    """

    class Outcome(models.TextChoices):
        SUCCESS = "success", "완료"
        DROPOUT = "dropout", "이탈"

    class Purpose(models.TextChoices):
        CALIBRATION = "calibration", "보정용"
        EVALUATION = "evaluation", "평가용"

    class Source(models.TextChoices):
        MANUAL = "manual", "직접 입력"
        RECORDER = "recorder", "기록 장치(recorder.js) 가져오기"

    project = models.ForeignKey(settings.EXITPLAIN_PROJECT_MODEL, on_delete=models.CASCADE,
                                related_name="human_observations")
    participant_code = models.CharField(max_length=40)
    task_id = models.CharField(max_length=80)
    outcome = models.CharField(max_length=8, choices=Outcome.choices)
    stage_id = models.CharField(max_length=300, blank=True, default="")   # 멈춘 화면 경로 (이탈만)
    cause = models.CharField(max_length=20, blank=True, default="")       # behavior.CAUSES 또는 unknown (이탈만)
    purpose = models.CharField(max_length=12, choices=Purpose.choices, default=Purpose.CALIBRATION)
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.MANUAL)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["participant_code", "task_id"]
        constraints = [models.UniqueConstraint(fields=["project", "participant_code", "task_id"],
                                               name="uniq_human_observation")]
