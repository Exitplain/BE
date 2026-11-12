"""요청 모양(타입·길이) 검사. 탐색 결과와 맞는지 같은 업무 규칙은 services.py에서 본다."""
from rest_framework import serializers

from . import catalog


class StrictSerializer(serializers.Serializer):
    """정의하지 않은 필드가 오면 422. 중첩 serializer에도 적용된다."""

    def to_internal_value(self, data):
        if isinstance(data, dict):
            unknown = set(data) - set(self.fields)
            if unknown:
                raise serializers.ValidationError({k: ["허용하지 않는 필드입니다."] for k in sorted(unknown)})
        return super().to_internal_value(data)


class PlanCreateSerializer(StrictSerializer):
    discovery_id = serializers.UUIDField()


class AudienceSerializer(StrictSerializer):
    context = serializers.CharField(max_length=catalog.TEXT_LIMIT, trim_whitespace=True,
                                    error_messages={"blank": "타겟 고객 설명을 입력해주세요."})
    ages = serializers.ListField(child=serializers.ChoiceField(choices=list(catalog.AGE_OPTIONS)),
                                 required=False, allow_empty=True)


class StageSerializer(StrictSerializer):
    id = serializers.CharField(max_length=40)
    name = serializers.CharField(max_length=catalog.STAGE_NAME_LIMIT,
                                 error_messages={"blank": "단계 이름을 입력해주세요."})
    order = serializers.IntegerField(min_value=0)


class TaskFunnelSerializer(StrictSerializer):
    task_id = serializers.CharField(max_length=40)
    stages = StageSerializer(many=True)


class SegmentSerializer(StrictSerializer):
    id = serializers.ChoiceField(choices=catalog.SEGMENT_IDS)
    count = serializers.IntegerField(min_value=1, error_messages={"min_value": "세그먼트마다 1명 이상 넣어주세요."})


class LimitsSerializer(StrictSerializer):
    viewport_width = serializers.IntegerField(required=False)
    concurrency = serializers.IntegerField(required=False)
    request_interval_seconds = serializers.FloatField(required=False)
    seed_count = serializers.IntegerField(required=False)
    max_steps = serializers.IntegerField(required=False)
    timeout_seconds = serializers.IntegerField(required=False)
    cost_cap = serializers.FloatField(required=False, allow_null=True, min_value=0.01)

    def validate(self, attrs):
        errors = {}
        for key, (low, high, _) in catalog.LIMIT_RANGES.items():
            if key in attrs and not low <= attrs[key] <= high:
                errors[key] = [f"{low}~{high} 사이 값을 입력해주세요."]
        if errors:
            raise serializers.ValidationError(errors)
        return attrs


class PlanPatchSerializer(StrictSerializer):
    """PATCH /plans/{id}. 보낸 필드만 바꾼다(모든 최상위 필드 선택). 2단계 필드와 3단계 필드를 따로 보내도 된다."""
    task_ids = serializers.ListField(child=serializers.CharField(max_length=40), allow_empty=False, required=False,
                                     error_messages={"empty": "과업을 한 개 이상 골라주세요."})
    custom_tasks = serializers.ListField(
        child=serializers.CharField(max_length=catalog.CUSTOM_TASK_TEXT_LIMIT), allow_empty=True,
        max_length=catalog.CUSTOM_TASK_LIMIT, required=False)
    additional_request = serializers.CharField(max_length=catalog.TEXT_LIMIT, allow_blank=True, required=False)
    audience = AudienceSerializer(required=False)
    task_funnels = TaskFunnelSerializer(many=True, required=False)
    segments = SegmentSerializer(many=True, required=False)
    frustration_enabled = serializers.BooleanField(required=False)
    limits = LimitsSerializer(required=False)
    # partial=True를 쓰지 않는다: 쓰면 중첩 필드의 필수 검사까지 꺼진다


class ConfirmationSerializer(StrictSerializer):
    revision = serializers.IntegerField(min_value=1)