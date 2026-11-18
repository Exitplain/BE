import uuid

from django.conf import settings
from django.db import models


class SourceConnection(models.Model):
    """프로젝트에 연결한 코드 출처 (SRC-01). 공개 git 저장소 주소 또는 zip 업로드. 읽기만 한다 (실행 권한 아님)."""

    class Provider(models.TextChoices):
        ZIP = "zip", "zip 업로드"
        GIT = "git", "공개 git 저장소"

    class Status(models.TextChoices):
        ACTIVE = "active", "연결됨"
        REVOKED = "revoked", "해제됨"

    project = models.ForeignKey(settings.EXITPLAIN_PROJECT_MODEL, on_delete=models.CASCADE, related_name="source_connections")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    provider = models.CharField(max_length=8, choices=Provider.choices)
    repository_url = models.URLField(max_length=300, blank=True, default="")
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class SourceSnapshot(models.Model):
    """한 시점에 고정한 코드 (SRC-02). git 은 commit SHA, zip 은 내용 해시(tree_digest)로 고정한다.

    받기·분석은 백그라운드에서 돌고 progress 에 진행 사건을 쌓는다 (common/progress.py).
    파일은 EXITPLAIN_SOURCE_ROOT/<id>/ 에 두고, 연결을 해제하면 지운다 (files_deleted_at).
    """

    class State(models.TextChoices):
        RUNNING = "running", "받는 중"
        READY = "ready", "준비됨"
        FAILED = "failed", "실패"

    class Runnable(models.TextChoices):
        STATIC = "static", "정적 사이트로 바로 열 수 있음"
        NEEDS_BUILD = "needs_build", "빌드가 필요함"
        UNKNOWN = "unknown", "판단할 수 없음"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    connection = models.ForeignKey(SourceConnection, on_delete=models.CASCADE, related_name="snapshots")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    ref = models.CharField(max_length=100, blank=True, default="")          # 요청한 브랜치·태그 (git)
    commit_sha = models.CharField(max_length=40, blank=True, default="")
    subdirectory = models.CharField(max_length=200, blank=True, default="")  # 모노레포에서 분석할 폴더
    tree_digest = models.CharField(max_length=64, blank=True, default="")
    file_count = models.PositiveIntegerField(default=0)
    total_bytes = models.BigIntegerField(default=0)
    skipped = models.JSONField(default=dict, blank=True)     # 저장하지 않은 파일 수 (이유별)
    state = models.CharField(max_length=8, choices=State.choices, default=State.RUNNING)
    runnable = models.CharField(max_length=12, choices=Runnable.choices, default=Runnable.UNKNOWN)
    static_root = models.CharField(max_length=200, blank=True, default="")   # 정적으로 열 폴더 (subdirectory 기준)
    framework = models.JSONField(default=dict, blank=True)
    analysis = models.JSONField(default=dict, blank=True)    # 라우트·화면 이동 (근거 file:line)
    progress = models.JSONField(default=list, blank=True)
    error_code = models.CharField(max_length=40, blank=True, default="")
    error_message = models.CharField(max_length=300, blank=True, default="")
    files_deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
