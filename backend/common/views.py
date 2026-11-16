from rest_framework.authentication import SessionAuthentication
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import JSONRenderer
from rest_framework.views import APIView

from .exceptions import exception_handler


class BaseAPIView(APIView):
    """B 앱 공통 뷰. 세션 로그인(Django 기본 인증) + CSRF, JSON만, 공통 오류 형식.

    settings의 REST_FRAMEWORK를 건드리지 않아도 되게 여기서 직접 지정한다.
    백엔드 A가 전역 설정을 만들면 이 클래스의 속성을 지워도 된다.
    """
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser]
    renderer_classes = [JSONRenderer]

    def get_exception_handler(self):
        return exception_handler