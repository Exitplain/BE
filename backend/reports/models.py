import uuid

from django.conf import settings
from django.db import models


class ReportSnapshot(models.Model):
    """한 번의 실행(Run)을 집계한 결과. 만든 뒤에는 바꾸지 않고, 다시 집계하면 revision이 올라간 새 행을 만든다.

    run: 백엔드 A의 Run (runs.Run). 실행이 끝나면 A의 워커가 finalize_run(run)을 불러 자동으로 만든다.
    run_ref: str(run.id). A의 runs/report_link.py가 이 값으로 리포트를 찾으므로 그대로 남긴다.
            샘플 실행 폴더로 만든 리포트(run=None)는 폴더 이름 등 문자열.
    """

    class Status(models.TextChoices):
        FINAL = "final", "완료"          # 예상한 세션이 모두 끝남
        PARTIAL = "partial", "부분"      # 아직 안 끝났거나 결과가 빠진 세션이 있음

    class Source(models.TextChoices):
        SAMPLE_FILES = "sample_files", "실행 결과 파일(events.jsonl)"
        DATABASE = "database", "백엔드 A의 Session·Event"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    plan_snapshot = models.ForeignKey("plans.PlanSnapshot", on_delete=models.PROTECT, related_name="reports")
    run = models.ForeignKey("runs.Run", on_delete=models.PROTECT, null=True, blank=True, related_name="reports")
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

        

class ShareLink(models.Model):
    """리포트 공유 링크 (11/17, Should). 링크를 가진 사람은 로그인 없이 리포트를 볼 수 있다.

    token은 추측할 수 없는 임의 문자열(secrets.token_urlsafe). 만료되거나 취소하면 더 이상 열리지 않는다.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    report = models.ForeignKey(ReportSnapshot, on_delete=models.CASCADE, related_name="share_links")
    token = models.CharField(max_length=64, unique=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]