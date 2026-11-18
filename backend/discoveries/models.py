import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class Discovery(models.Model):
    """위저드 1단계(P02·P03) 결과. 사이트에서 찾은 기능·과업 후보. 15분 뒤 만료.

    비동기로 시작하면 running 으로 만들어 두고 둘러보는 동안 progress 에 진행 사건을 쌓는다.
    """

    class LoginMode(models.TextChoices):
        NONE = "none", "로그인 없이 테스트"
        TEST_ACCOUNT = "test_account", "테스트 계정 로그인 상태로 테스트"

    class Status(models.TextChoices):
        RUNNING = "running", "확인 중"
        SUCCEEDED = "succeeded", "완료"
        FAILED = "failed", "실패"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(settings.EXITPLAIN_PROJECT_MODEL, on_delete=models.CASCADE, related_name="discoveries")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    entry_url = models.URLField(max_length=500)
    # 코드로 확인한 경우 받은 스냅샷 (entry_url 은 그 미리보기 주소). URL 로 확인했으면 비어 있다
    source_snapshot = models.ForeignKey("sources.SourceSnapshot", null=True, blank=True, on_delete=models.SET_NULL,
                                        related_name="discoveries")
    login_mode = models.CharField(max_length=16, choices=LoginMode.choices, default=LoginMode.NONE)
    variant = models.CharField(max_length=16)          # 데모 쇼핑몰 버전 fixed/problem
    features = models.JSONField(default=list)          # [{id, name, evidence, verification}]
    tasks = models.JSONField(default=list)             # [{id, name, goal, case, success_path, feature_id, suggested}]
    screen_names = models.JSONField(default=dict, blank=True)   # 사이트 확인(explore)이 붙인 화면 역할 이름 {정규화 경로: 이름}
    note = models.CharField(max_length=300, blank=True, default="")
    elapsed_seconds = models.FloatField(null=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.SUCCEEDED)
    progress = models.JSONField(default=list, blank=True)   # [{seq, t, kind, message, ...}] 최근 것만 (services.PROGRESS_LIMIT)
    error_code = models.CharField(max_length=40, blank=True, default="")
    error_message = models.CharField(max_length=300, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)        # 진행 중이면 주기적으로 갱신된다 (멈춤 감지)
    expires_at = models.DateTimeField()

    class Meta:
        ordering = ["-created_at"]

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at

    def task_map(self):
        return {t["id"]: t for t in self.tasks}

# Create your models here.
