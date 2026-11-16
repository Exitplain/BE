"""docs/API_SPEC.md 8절 오류 형식: {"error": {"code", "message", "fields"}, "request_id"}"""
import uuid

from django.http import Http404
from rest_framework import exceptions, status
from rest_framework.response import Response


class ApiError(exceptions.APIException):
    def __init__(self, status_code, code, message, fields=None):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.fields = fields or {}
        super().__init__(message)


def validation_error(field, message):
    return ApiError(422, "VALIDATION_ERROR", message, {field: [message]})


def _flatten(detail, prefix=""):
    """DRF ValidationError detail → {"필드.경로": ["메시지"]}"""
    out = {}
    if isinstance(detail, dict):
        for key, value in detail.items():
            name = f"{prefix}.{key}" if prefix else str(key)
            if key == "non_field_errors":
                name = prefix or "non_field_errors"
            out.update(_flatten(value, name))
    elif isinstance(detail, list):
        if all(not isinstance(v, (dict, list)) for v in detail):
            out[prefix or "non_field_errors"] = [str(v) for v in detail]
        else:
            for i, value in enumerate(detail):
                out.update(_flatten(value, f"{prefix}.{i}" if prefix else str(i)))
    else:
        out[prefix or "non_field_errors"] = [str(detail)]
    return out


def _body(code, message, fields=None):
    return {"error": {"code": code, "message": message, "fields": fields or {}},
            "request_id": "req_" + uuid.uuid4().hex[:12]}


def exception_handler(exc, context):
    if isinstance(exc, ApiError):
        return Response(_body(exc.code, exc.message, exc.fields), status=exc.status_code)
    if isinstance(exc, exceptions.NotAuthenticated) or isinstance(exc, exceptions.AuthenticationFailed):
        return Response(_body("AUTH_REQUIRED", "로그인이 필요합니다."), status=status.HTTP_401_UNAUTHORIZED)
    if isinstance(exc, exceptions.PermissionDenied):
        return Response(_body("FORBIDDEN", "이 리소스에 접근할 권한이 없습니다."), status=status.HTTP_403_FORBIDDEN)
    if isinstance(exc, (Http404, exceptions.NotFound)):
        return Response(_body("NOT_FOUND", "대상을 찾을 수 없습니다."), status=status.HTTP_404_NOT_FOUND)
    if isinstance(exc, exceptions.ParseError):
        return Response(_body("INVALID_JSON", "요청 본문이 올바른 JSON이 아닙니다."), status=status.HTTP_400_BAD_REQUEST)
    if isinstance(exc, exceptions.ValidationError):
        fields = _flatten(exc.detail)
        first = next(iter(fields.values()))[0] if fields else "입력값을 확인해주세요."
        return Response(_body("VALIDATION_ERROR", first, fields), status=422)
    if isinstance(exc, exceptions.MethodNotAllowed):
        return Response(_body("METHOD_NOT_ALLOWED", "지원하지 않는 요청 방식입니다."), status=405)
    if isinstance(exc, exceptions.APIException):
        return Response(_body("ERROR", str(exc.detail)), status=exc.status_code)
    return None  # 그 밖의 예외는 Django 기본 처리(500)