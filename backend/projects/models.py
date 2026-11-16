"""프로젝트 = 고객이 테스트할 사이트 1개 (명세서 4. 프로젝트). 백엔드 A 담당.

B 앱이 읽는 값은 owner / entry_url / allowed_origins 뿐이다 (common/projects.py).
앱 이름과 `projects.Project` 경로는 B의 임시 모델과 같게 유지한다 (settings.EXITPLAIN_PROJECT_MODEL).
"""
from django.conf import settings
from django.db import models


class Project(models.Model):
    class LoginMode(models.TextChoices):
        NONE = "none", "로그인 없이 테스트"
        TEST_ACCOUNT = "test_account", "테스트 계정 로그인 상태로 테스트"

    class CheckStatus(models.TextChoices):
        UNCHECKED = "unchecked", "확인 전"
        OK = "ok", "접속 가능"
        FAILED = "failed", "접속 불가"

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="projects")
    name = models.CharField(max_length=100)
    entry_url = models.URLField(max_length=500)
    allowed_origins = models.JSONField(default=list, blank=True)
    login_mode = models.CharField(max_length=16, choices=LoginMode.choices, default=LoginMode.NONE)

    # 5.0 URL 확인 결과 (형식·SSRF·접근성만. 사이트 분석은 B의 Discovery)
    check_status = models.CharField(max_length=16, choices=CheckStatus.choices, default=CheckStatus.UNCHECKED)
    check_http = models.PositiveSmallIntegerField(null=True, blank=True)
    check_ms = models.PositiveIntegerField(null=True, blank=True)
    check_checked_at = models.DateTimeField(null=True, blank=True)

    journey_note = models.TextField(blank=True, default="")
    is_sample = models.BooleanField(default=False)  # 1.0.1 샘플 프로젝트 (반찬로드)
    archived_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["owner", "-created_at"])]

    def __str__(self):
        return self.name

    @property
    def is_archived(self):
        return self.archived_at is not None
