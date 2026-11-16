from rest_framework import serializers

from .models import Run, Session
from .services import PERIOD_DAYS


def _csv_choice_field(choices, label):
    """`?status=running,completed` 처럼 쉼표로 이은 값을 목록으로 받고, 허용 값이 아니면 422."""

    class Field(serializers.CharField):
        def to_internal_value(self, data):
            parts = [p.strip() for p in super().to_internal_value(data).split(",") if p.strip()]
            invalid = [p for p in parts if p not in choices]
            if invalid:
                raise serializers.ValidationError(f"{label} 값이 올바르지 않습니다: {', '.join(invalid)}")
            return parts

    return Field(required=False, allow_blank=True, default=list)


class StrictSerializer(serializers.Serializer):
    """정의하지 않은 필드가 오면 422 (plans·projects 앱과 같은 규칙)."""

    def to_internal_value(self, data):
        if isinstance(data, dict):
            unknown = set(data) - set(self.fields)
            if unknown:
                raise serializers.ValidationError({k: ["허용하지 않는 필드입니다."] for k in sorted(unknown)})
        return super().to_internal_value(data)


class RunListQuerySerializer(serializers.Serializer):
    q = serializers.CharField(required=False, allow_blank=True, max_length=100, default="")
    status = _csv_choice_field(set(Run.Status.values), "상태")
    kind = _csv_choice_field(set(Run.Kind.values), "유형")
    period = serializers.ChoiceField(choices=[*PERIOD_DAYS, "all"], required=False, default="all")
    limit = serializers.IntegerField(required=False, min_value=1, max_value=100, default=20)
    cursor = serializers.CharField(required=False, allow_blank=True, max_length=200, default="")


class RunCreateSerializer(StrictSerializer):
    plan_revision_id = serializers.UUIDField(error_messages={"required": "확정한 계획을 선택해주세요.",
                                                             "invalid": "계획 ID가 올바르지 않습니다."})
    base_run_id = serializers.UUIDField(required=False, allow_null=True, default=None,
                                        error_messages={"invalid": "실행 ID가 올바르지 않습니다."})
    change_note = serializers.CharField(required=False, allow_blank=True, max_length=500, default="", trim_whitespace=True)


class SessionListQuerySerializer(serializers.Serializer):
    status = _csv_choice_field(set(Session.Status.values), "상태")
    limit = serializers.IntegerField(required=False, min_value=1, max_value=200, default=50)
    cursor = serializers.CharField(required=False, allow_blank=True, max_length=200, default="")


class EventListQuerySerializer(serializers.Serializer):
    after = serializers.IntegerField(required=False, min_value=0, default=0)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=500, default=100)
