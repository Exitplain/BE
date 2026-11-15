"""테스트 계정 비밀번호 암호화.

키: settings.TEST_ACCOUNT_KEY (Fernet 키)가 있으면 그것을, 없으면 SECRET_KEY에서 만든 키를 쓴다.
서버는 DJANGO_SECRET_KEY가 필수라 .env를 따로 고치지 않아도 된다.
주의: 키(SECRET_KEY)가 바뀌면 저장된 비밀번호를 풀 수 없다 → 계정을 다시 입력해야 한다.
"""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


class DecryptError(ValueError):
    pass


def _fernet():
    key = getattr(settings, "TEST_ACCOUNT_KEY", "") or ""
    if not key:
        digest = hashlib.sha256(f"exitplain-test-accounts:{settings.SECRET_KEY}".encode()).digest()
        key = base64.urlsafe_b64encode(digest).decode()
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(text):
    return _fernet().encrypt(text.encode()).decode()


def decrypt(token):
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise DecryptError("저장된 비밀번호를 풀 수 없습니다. 테스트 계정을 다시 입력해주세요.") from exc