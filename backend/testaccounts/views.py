"""테스트 계정 API (Figma P04 테스트 계정 등록).

GET /api/v1/projects/{id}/test-accounts          계정 목록 (비밀번호 없음, 계정별 자동 삭제 예정일 expires_at)
GET /api/v1/projects/{id}/test-accounts/events   감사 기록
PUT /api/v1/projects/{id}/test-accounts   계정 목록 전체 저장
    {login_url?, accounts: [{id?, username, password?}]}   (최대 20개, 보낸 순서 = 페르소나 짝 순서)
"""
from rest_framework import serializers
from rest_framework.response import Response

from common.projects import get_owned_project
from common.views import BaseAPIView

from . import services


class StrictSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        if isinstance(data, dict):
            unknown = set(data) - set(self.fields)
            if unknown:
                raise serializers.ValidationError({k: ["허용하지 않는 필드입니다."] for k in sorted(unknown)})
        return super().to_internal_value(data)


class AccountItemSerializer(StrictSerializer):
    id = serializers.UUIDField(required=False, allow_null=True)
    username = serializers.CharField(max_length=200, trim_whitespace=True)
    password = serializers.CharField(max_length=200, required=False, allow_blank=True, trim_whitespace=False)


class AccountsSerializer(StrictSerializer):
    login_url = serializers.CharField(max_length=500, required=False, allow_blank=True)
    accounts = AccountItemSerializer(many=True)


class ProjectTestAccountEventView(BaseAPIView):
    """GET /api/v1/projects/{id}/test-accounts/events  감사 기록 최근 100개 (소유자만)"""

    def get(self, request, project_id):
        return Response(services.events_dict(get_owned_project(request.user, project_id)))


class ProjectTestAccountView(BaseAPIView):
    def get(self, request, project_id):
        return Response(services.accounts_dict(get_owned_project(request.user, project_id)))

    def put(self, request, project_id):
        project = get_owned_project(request.user, project_id)
        serializer = AccountsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        return Response(services.replace_accounts(project, request.user, data.get("login_url", ""), data["accounts"]))