"""요청 모양(타입·길이·URL 형식) 검사. 오류 문구는 Figma C04b(입력 오류) 화면의 문구를 그대로 쓴다.

C04b 는 이름·주소 오류를 한 번에 두 칸 모두 보여주므로, 형식 검사는 모두 여기서 한다.
"""
from rest_framework import serializers

from common.exceptions import ApiError

from .services import normalize_entry_url

NAME_REQUIRED = "프로젝트 이름을 입력해주세요"


class StrictSerializer(serializers.Serializer):
    """정의하지 않은 필드가 오면 422 (plans 앱과 같은 규칙. projects 가 plans 를 import 하지 않으려고 따로 둔다)."""

    def to_internal_value(self, data):
        if isinstance(data, dict):
            unknown = set(data) - set(self.fields)
            if unknown:
                raise serializers.ValidationError({k: ["허용하지 않는 필드입니다."] for k in sorted(unknown)})
        return super().to_internal_value(data)


class ProjectCreateSerializer(StrictSerializer):
    name = serializers.CharField(max_length=100, trim_whitespace=True, error_messages={
        "required": NAME_REQUIRED, "blank": NAME_REQUIRED, "null": NAME_REQUIRED,
        "max_length": "프로젝트 이름은 100자 이하로 입력해주세요"})
    # 사이트 URL 은 선택이다. 비우면 주소 없는 프로젝트가 되고, 새 테스트 1단계에서 주소를 입력한다.
    entry_url = serializers.CharField(max_length=500, trim_whitespace=True, required=False, allow_blank=True,
                                      allow_null=True, default="", error_messages={
        "max_length": "사이트 URL은 500자 이하로 입력해주세요"})
    journey_note = serializers.CharField(max_length=300, required=False, allow_blank=True, default="",
                                         trim_whitespace=True, error_messages={
        "max_length": "점검할 고객 여정은 300자 이하로 입력해주세요"})

    def validate_entry_url(self, value):
        if not value:        # 비움(빈 문자열·null·생략) = 주소 없는 프로젝트
            return ""
        try:
            return normalize_entry_url(value)
        except ApiError as exc:
            raise serializers.ValidationError(exc.message) from None


class ProjectListQuerySerializer(serializers.Serializer):
    q = serializers.CharField(required=False, allow_blank=True, max_length=100, default="")
    archived = serializers.BooleanField(required=False, default=False)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=100, default=20)
    cursor = serializers.CharField(required=False, allow_blank=True, max_length=200, default="")
