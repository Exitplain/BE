import uuid

from django.db import models


class PersonaSnapshot(models.Model):
    """확정 시점에 만든 페르소나 1명 (Figma P07의 P-001 ~ P-020). 만든 뒤에는 바꾸지 않는다."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    plan_snapshot = models.ForeignKey("plans.PlanSnapshot", on_delete=models.CASCADE, related_name="personas")
    code = models.CharField(max_length=10)              # P-001
    segment_id = models.CharField(max_length=20)        # segment_1
    segment_name = models.CharField(max_length=40)      # 빠른 구매형
    is_representative = models.BooleanField(default=False)
    # 행동 변수 0~100: patience, digital_literacy, price_sensitivity, trust_need
    variables = models.JSONField(default=dict)
    context = models.CharField(max_length=1000)         # 에이전트에 넘기는 설명
    generator = models.CharField(max_length=120)        # fixture-*는 실제 생성값이 아님
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["plan_snapshot", "code"]
        constraints = [models.UniqueConstraint(fields=["plan_snapshot", "code"], name="uniq_persona_code")]