"""테스트 계정 (Figma P04 테스트 계정 등록 · P04b 직접 입력 · P04d 붙여넣기 · P05h 계정 수 = 페르소나 수).

로그인이 필요한 사이트를 점검할 때, 계정 1개로 에이전트(페르소나) 1명이 로그인한다.
비밀번호는 암호화해서 저장하고 API 응답에는 절대 내보내지 않는다.
"""
import uuid

from django.conf import settings
from django.db import models


class TestAccountSet(models.Model):
    """프로젝트마다 하나. 로그인 화면 주소를 둔다."""
    project = models.OneToOneField("projects.Project", on_delete=models.CASCADE, related_name="test_account_set")
    login_url = models.URLField(max_length=500, blank=True, default="")
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)


class TestAccount(models.Model):
    class Status(models.TextChoices):
        UNCHECKED = "unchecked", "확인 전"
        OK = "ok", "확인됨"
        FAILED = "failed", "실패"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="test_accounts")
    position = models.PositiveSmallIntegerField()            # 화면 순서 (1부터). 페르소나와 이 순서로 짝짓는다
    username = models.CharField(max_length=200)
    password_encrypted = models.TextField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.UNCHECKED)
    status_message = models.CharField(max_length=200, blank=True, default="")
    checked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["project", "position"]
        constraints = [models.UniqueConstraint(fields=["project", "username"], name="uniq_test_account_username")]

    def __str__(self):
        return f"{self.position}. {self.username}"