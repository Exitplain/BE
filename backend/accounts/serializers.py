from rest_framework import serializers


class StrictSerializer(serializers.Serializer):
    """정의하지 않은 필드가 오면 422."""

    def to_internal_value(self, data):
        if isinstance(data, dict):
            unknown = set(data) - set(self.fields)
            if unknown:
                raise serializers.ValidationError({k: ["허용하지 않는 필드입니다."] for k in sorted(unknown)})
        return super().to_internal_value(data)


class EmailRequestSerializer(StrictSerializer):
    email = serializers.EmailField(max_length=254, error_messages={"invalid": "이메일 주소를 확인해 주세요."})


class EmailVerifySerializer(StrictSerializer):
    email = serializers.EmailField(max_length=254)
    code = serializers.RegexField(r"^\d{6}$", error_messages={"invalid": "6자리 숫자를 입력해 주세요."})


class GoogleLoginSerializer(StrictSerializer):
    id_token = serializers.CharField(max_length=4096)


class WorkspaceUpdateSerializer(StrictSerializer):
    """W04 워크스페이스 설정: 워크스페이스 이름, 내 이름. 이메일은 바꿀 수 없음."""
    workspace_name = serializers.CharField(max_length=50, required=False,
                                           error_messages={"blank": "워크스페이스 이름을 입력해 주세요."})
    my_name = serializers.CharField(max_length=50, required=False, allow_blank=True)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("바꿀 항목을 한 개 이상 보내 주세요.")
        return attrs