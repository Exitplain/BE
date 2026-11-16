import uuid

from django.conf import settings
from django.db import models


class TestPlan(models.Model):
    """새 테스트 계획 (위저드 2·3단계에서 수정). 확정할 때마다 PlanSnapshot이 하나씩 생긴다.

    2 과업·타겟 (P05)      : task_ids, custom_tasks, additional_request, audience_context, ages
    3 퍼널·페르소나 (P05b) : task_funnels, segments, frustration_enabled, limits
    """
    __test__ = False  # pytest가 Test*로 수집하지 않게

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(settings.EXITPLAIN_PROJECT_MODEL, on_delete=models.CASCADE, related_name="test_plans")
    discovery = models.ForeignKey("discoveries.Discovery", on_delete=models.PROTECT, related_name="plans")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    revision = models.PositiveIntegerField(default=1)  # If-Match 비교용. 수정할 때마다 +1

    # 2. 과업·타겟
    task_ids = models.JSONField(default=list)
    custom_tasks = models.JSONField(default=list)     # [{id, text, state: "draft"}] 실행 미포함
    additional_request = models.TextField(blank=True, default="")
    audience_context = models.TextField(blank=True, default="")  # 타겟 고객 설명 (Figma 필수)
    ages = models.JSONField(default=list)

    # 3. 퍼널·페르소나
    task_funnels = models.JSONField(default=list)     # 자동 7단계 (편집 없음). DB 칸은 마이그레이션 없이 남겨 둔다
    segments = models.JSONField(default=list)         # [{id: "segment_1", count: 5}, ...]
    frustration_enabled = models.BooleanField(default=True)   # Figma에서 토글이 빠져 항상 True
    limits = models.JSONField(default=dict)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["project", "-created_at"])]


class PlanSnapshot(models.Model):
    """확정 스냅샷 (P05c 확정). 만든 뒤에는 바꾸지 않는다. 실행(Run)은 이 ID를 참조한다.

    API_SPEC 8절의 plan_revision_id가 이 모델의 id다.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    plan = models.ForeignKey(TestPlan, on_delete=models.PROTECT, related_name="snapshots")
    plan_revision = models.PositiveIntegerField()     # 확정 당시 TestPlan.revision
    data = models.JSONField()                         # 실행에 필요한 모든 설정의 고정본
    persona_generator = models.CharField(max_length=120)
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    confirmed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-confirmed_at"]
        constraints = [models.UniqueConstraint(fields=["plan", "plan_revision"], name="uniq_snapshot_per_revision")]