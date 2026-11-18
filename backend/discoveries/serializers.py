from rest_framework import serializers

from .models import Discovery

VARIANTS = ("fixed", "problem")


class DiscoveryCreateSerializer(serializers.Serializer):
    entry_url = serializers.URLField(required=False, max_length=500)
    login_mode = serializers.ChoiceField(choices=Discovery.LoginMode.choices, default=Discovery.LoginMode.NONE)
    variant = serializers.ChoiceField(choices=VARIANTS, default="problem")
    # 코드로 확인 (URL 없는 정적 사이트). 있으면 entry_url 은 쓰지 않는다
    source_snapshot_id = serializers.UUIDField(required=False)

    def validate(self, attrs):
        unknown = set(self.initial_data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError({k: ["허용하지 않는 필드입니다."] for k in sorted(unknown)})
        return attrs


def discovery_dict(d):
    return {
        "id": str(d.id), "project_id": d.project_id, "entry_url": d.entry_url, "login_mode": d.login_mode,
        "variant": d.variant, "status": d.status,
        "source_snapshot_id": str(d.source_snapshot_id) if d.source_snapshot_id else None, "progress": d.progress,
        "error": {"code": d.error_code, "message": d.error_message} if d.error_code else None,
        "features": d.features, "tasks": d.tasks, "note": d.note, "screen_names": d.screen_names,
        "elapsed_seconds": d.elapsed_seconds, "created_at": d.created_at.isoformat(),
        "updated_at": d.updated_at.isoformat(), "expires_at": d.expires_at.isoformat(), "expired": d.is_expired,
    }