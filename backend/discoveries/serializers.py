from rest_framework import serializers

from .models import Discovery

VARIANTS = ("fixed", "problem")


class DiscoveryCreateSerializer(serializers.Serializer):
    entry_url = serializers.URLField(required=False, max_length=500)
    login_mode = serializers.ChoiceField(choices=Discovery.LoginMode.choices, default=Discovery.LoginMode.NONE)
    variant = serializers.ChoiceField(choices=VARIANTS, default="problem")

    def validate(self, attrs):
        unknown = set(self.initial_data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError({k: ["허용하지 않는 필드입니다."] for k in sorted(unknown)})
        return attrs


def discovery_dict(d):
    return {
        "id": str(d.id), "project_id": d.project_id, "entry_url": d.entry_url, "login_mode": d.login_mode,
        "variant": d.variant, "features": d.features, "tasks": d.tasks, "note": d.note,
        "elapsed_seconds": d.elapsed_seconds, "created_at": d.created_at.isoformat(),
        "expires_at": d.expires_at.isoformat(), "expired": d.is_expired,
    }