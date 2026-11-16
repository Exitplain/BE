import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class Discovery(models.Model):
    """위저드 1단계(P02·P03) 결과. 사이트에서 찾은 기능·과업 후보. 15분 뒤 만료."""

    class LoginMode(models.TextChoices):
        NONE = "none", "로그인 없이 테스트"
        TEST_ACCOUNT = "test_account", "테스트 계정 로그인 상태로 테스트"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(settings.EXITPLAIN_PROJECT_MODEL, on_delete=models.CASCADE, related_name="discoveries")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    entry_url = models.URLField(max_length=500)
    login_mode = models.CharField(max_length=16, choices=LoginMode.choices, default=LoginMode.NONE)
    variant = models.CharField(max_length=16)          # 데모 쇼핑몰 버전 fixed/problem
    features = models.JSONField(default=list)          # [{id, name, evidence, verification}]
    tasks = models.JSONField(default=list)             # [{id, name, goal, case, success_path, feature_id, suggested}]
    note = models.CharField(max_length=300, blank=True, default="")
    elapsed_seconds = models.FloatField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    class Meta:
        ordering = ["-created_at"]

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at

    def task_map(self):
        return {t["id"]: t for t in self.tasks}

# Create your models here.
