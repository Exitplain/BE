"""Google 로그인 (L01 'Google로 계속하기').

프론트가 Google Identity Services로 받은 ID 토큰(credential)을 보내면, 백엔드가 Google 공개키로 검증한다.
필요한 것: pip install google-auth requests, 환경변수 GOOGLE_OAUTH_CLIENT_ID (Google Cloud Console에서 발급)
"""
from django.conf import settings
from django.utils.module_loading import import_string


class GoogleTokenError(ValueError):
    pass


def verify_id_token(token):
    """검증에 성공하면 {"sub", "email", "name"}을 돌려준다."""
    client_id = getattr(settings, "GOOGLE_OAUTH_CLIENT_ID", "")
    if not client_id:
        raise GoogleTokenError("GOOGLE_OAUTH_CLIENT_ID가 설정되지 않았습니다.")
    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token
        info = id_token.verify_oauth2_token(token, google_requests.Request(), client_id)
    except ImportError as exc:
        raise GoogleTokenError("google-auth 패키지가 설치되지 않았습니다.") from exc
    except ValueError as exc:  # 서명·만료·대상(client_id) 불일치
        raise GoogleTokenError("Google 토큰을 확인하지 못했습니다.") from exc
    if info.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
        raise GoogleTokenError("Google에서 발급한 토큰이 아닙니다.")
    if not info.get("email") or not info.get("email_verified"):
        raise GoogleTokenError("Google 계정의 이메일이 확인되지 않았습니다.")
    return {"sub": info["sub"], "email": info["email"], "name": info.get("name", "")}


def verifier():
    """테스트에서 바꿔 끼울 수 있게 settings.EXITPLAIN_GOOGLE_VERIFIER 경로를 따른다."""
    path = getattr(settings, "EXITPLAIN_GOOGLE_VERIFIER", "accounts.google.verify_id_token")
    return import_string(path)