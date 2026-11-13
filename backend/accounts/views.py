"""로그인·세션·워크스페이스 API.

POST /api/v1/auth/email/request   이메일로 6자리 코드 보내기 (L02)        {email}
POST /api/v1/auth/email/verify    코드 확인 → 로그인 (L03·L04·L05)        {email, code}
POST /api/v1/auth/google          Google ID 토큰 → 로그인 (L01)            {id_token}
GET  /api/v1/auth/session         지금 로그인 상태·만료 시각 (L08·L09)
POST /api/v1/auth/logout          로그아웃 (L08 메뉴)
GET  /api/v1/auth/csrf            CSRF 쿠키 받기 (프론트가 POST 전에 한 번)
GET  /api/v1/workspace            워크스페이스 설정 (W04)
PATCH /api/v1/workspace           워크스페이스 이름·내 이름 저장 (W04b)
"""
from django.contrib.auth import login, logout
from django.middleware.csrf import get_token
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from common.exceptions import ApiError
from common.views import BaseAPIView

from . import services
from .serializers import (EmailRequestSerializer, EmailVerifySerializer, GoogleLoginSerializer,
                          WorkspaceUpdateSerializer)


class PublicAPIView(BaseAPIView):
    """로그인 전에도 부를 수 있는 API. 로그인 요청은 CSRF를 강제로 검사한다 (로그인 CSRF 방지)."""
    permission_classes = [AllowAny]


def _login(request, user, method):
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")  # 세션 키도 새로 발급됨
    request.session["login_method"] = method
    return Response(services.session_dict(request))


@method_decorator(ensure_csrf_cookie, name="dispatch")
class CsrfView(PublicAPIView):
    def get(self, request):
        return Response({"csrf_token": get_token(request)})


@method_decorator(csrf_protect, name="dispatch")
class EmailRequestView(PublicAPIView):
    def post(self, request):
        serializer = EmailRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = services.request_email_code(serializer.validated_data["email"])
        return Response(result, status=status.HTTP_202_ACCEPTED)


@method_decorator(csrf_protect, name="dispatch")
class EmailVerifyView(PublicAPIView):
    def post(self, request):
        serializer = EmailVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = services.verify_email_code(serializer.validated_data["email"], serializer.validated_data["code"])
        return _login(request, user, "email_code")


@method_decorator(csrf_protect, name="dispatch")
class GoogleLoginView(PublicAPIView):
    def post(self, request):
        serializer = GoogleLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = services.login_with_google(serializer.validated_data["id_token"])
        return _login(request, user, "google")


class SessionView(PublicAPIView):
    """로그인 안 했으면 401 대신 authenticated=false (프론트가 첫 화면에서 상태 확인용으로 부름)."""

    def get(self, request):
        return Response(services.session_dict(request))


class LogoutView(BaseAPIView):
    def post(self, request):
        logout(request)  # 서버 세션 삭제 → 같은 쿠키로 다시 못 씀
        return Response(status=status.HTTP_204_NO_CONTENT)


class WorkspaceView(BaseAPIView):
    def _payload(self, request, membership):
        from plans.catalog import DEFAULT_LIMITS  # W04 '실행 기본값' (지금은 보기 전용)
        return {"workspace": services.workspace_dict(membership), "me": services.user_dict(request.user),
                "run_defaults": DEFAULT_LIMITS}

    def get(self, request):
        services.ensure_workspace(request.user)
        return Response(self._payload(request, services.current_membership(request.user)))

    def patch(self, request):
        serializer = WorkspaceUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        services.ensure_workspace(request.user)
        membership = services.current_membership(request.user)
        if "workspace_name" in data:
            if membership.role != membership.Role.OWNER:
                raise ApiError(403, "FORBIDDEN", "워크스페이스 이름은 관리 권한이 있어야 바꿀 수 있어요.")
            membership.workspace.name = data["workspace_name"].strip()
            membership.workspace.save(update_fields=["name", "updated_at"])
        if "my_name" in data:
            request.user.first_name = data["my_name"].strip()
            request.user.save(update_fields=["first_name"])
        return Response(self._payload(request, membership))