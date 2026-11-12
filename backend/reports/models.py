import uuid

from django.conf import settings
from django.db import models


class ReportSnapshot(models.Model):
    """한 번의 실행(Run)을 집계한 결과. 만든 뒤에는 바꾸지 않고, 다시 집계하면 revision이 올라간 새 행을 만든다.

    run_ref: 백엔드 A의 Run ID. A의 Run 모델이 생기면 FK로 바꾼다. 지금은 샘플 실행 폴더 이름 등 문자열.
    """

    class Status(models.TextChoices):
        FINAL = "final", "완료"          # 예상한 세션이 모두 끝남
        PARTIAL = "partial", "부분"      # 아직 안 끝났거나 결과가 빠진 세션이 있음

    class Source(models.TextChoices):
        SAMPLE_FILES = "sample_files", "실행 결과 파일(events.jsonl)"
        DATABASE = "database", "백엔드 A의 Session·Event"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    plan_snapshot = models.ForeignKey("plans.PlanSnapshot", on_delete=models.PROTECT, related_name="reports")
    run_ref = models.CharField(max_length=100)
    revision = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=10, choices=Status.choices)
    source = models.CharField(max_length=20, choices=Source.choices)
    data = models.JSONField()          # 집계 결과 (aggregate.build_report_data 출력)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["run_ref", "revision"], name="uniq_report_revision")]
        indexes = [models.Index(fields=["run_ref", "-revision"])]