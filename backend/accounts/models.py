"""로그인·워크스페이스 모델. 사용자는 Django 기본 User(username = 이메일)를 쓴다."""
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class Workspace(models.Model):
    """워크스페이스. 첫 로그인 때 사용자마다 하나씩 만든다 (초대·여러 멤버는 아직 없음)."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=50)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class WorkspaceMember(models.Model):
    """워크스페이스 소속과 역할. 지금은 owner만 쓰지만, 기능명세서 ACC-02(역할 검사)를 위해 표로 둔다."""

    class Role(models.TextChoices):
        OWNER = "owner", "관리"
        MEMBER = "member", "실행"
        VIEWER = "viewer", "읽기"

    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="members")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="workspace_memberships")
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.OWNER)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["workspace", "user"], name="uniq_workspace_member")]


class EmailLoginCode(models.Model):
    """이메일로 보낸 6자리 로그인 코드 (L02·L03). 코드 자체는 저장하지 않고 해시만 저장한다."""
    email = models.EmailField()
    code_hash = models.CharField(max_length=64)
    attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["email", "-created_at"])]

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at


class ExternalIdentity(models.Model):
    """외부 로그인 연결 (Google). 이메일이 바뀌어도 같은 사람으로 찾도록 provider의 고유 ID(sub)로 묶는다."""

    class Provider(models.TextChoices):
        GOOGLE = "google", "Google"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="external_identities")
    provider = models.CharField(max_length=20, choices=Provider.choices)
    subject = models.CharField(max_length=255)
    email = models.EmailField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["provider", "subject"], name="uniq_external_identity")]