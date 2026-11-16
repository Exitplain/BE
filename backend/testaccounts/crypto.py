"""테스트 계정 비밀번호 암호화.

두 가지 방식이 있다.

1) AWS KMS 봉투 암호화 (서버 운영용) — settings.TEST_ACCOUNT_KMS_KEY_ID 가 있으면 이 방식으로 저장한다.
   - 계정을 저장할 때마다 KMS에 GenerateDataKey를 불러 '데이터 키'를 받는다.
   - 비밀번호는 데이터 키로 AES-256-GCM 암호화하고, 데이터 키는 KMS가 암호화한 상태로만 같이 저장한다.
   - 풀 때는 KMS에 Decrypt를 불러 데이터 키를 되찾는다. KMS 키 자체는 AWS 밖으로 나오지 않는다.
   - 암호화 컨텍스트 {"project": 프로젝트 id}를 걸어서, 다른 프로젝트 이름으로는 풀 수 없다.
   - 서버(EC2)에 붙은 IAM 역할 권한으로 KMS를 부른다. 액세스 키를 .env에 넣지 않는다.
   저장 형식:  kms1:<KMS가 암호화한 데이터 키(base64)>:<nonce+암호문(base64)>

2) Fernet (개발·테스트용, 예전 데이터) — KMS 설정이 없으면 이 방식.
   키: settings.TEST_ACCOUNT_KEY 가 있으면 그것을, 없으면 SECRET_KEY에서 만든 키를 쓴다.

풀 때는 저장된 값 앞부분(kms1:)을 보고 알아서 고른다 → KMS로 바꾼 뒤에도 예전 Fernet 데이터를 읽는다.
예전 데이터를 KMS로 옮기려면: python manage.py reencrypt_test_accounts
"""
import base64
import hashlib
import os

from cryptography.exceptions import InvalidTag
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.conf import settings

KMS_PREFIX = "kms1:"
_BAD_KEY_CODES = {"InvalidCiphertextException", "IncorrectKeyException"}


class DecryptError(ValueError):
    """저장된 비밀번호를 풀 수 없음 (키가 바뀌었거나 다른 프로젝트 값)."""


class KeyUnavailable(RuntimeError):
    """KMS를 부르지 못함 (권한·네트워크·설정 문제)."""


# ---------- Fernet (개발용·예전 데이터) ----------

def _fernet():
    key = getattr(settings, "TEST_ACCOUNT_KEY", "") or ""
    if not key:
        digest = hashlib.sha256(f"exitplain-test-accounts:{settings.SECRET_KEY}".encode()).digest()
        key = base64.urlsafe_b64encode(digest).decode()
    return Fernet(key.encode() if isinstance(key, str) else key)


# ---------- KMS ----------

def kms_key_id():
    return getattr(settings, "TEST_ACCOUNT_KMS_KEY_ID", "") or ""


def uses_kms():
    return bool(kms_key_id())


_client = None


def _kms():
    """boto3 KMS 클라이언트 (한 번 만들어 재사용). 테스트에서는 이 함수를 바꿔 끼운다."""
    global _client
    if _client is None:
        import boto3  # KMS를 쓸 때만 필요
        _client = boto3.client("kms", region_name=getattr(settings, "TEST_ACCOUNT_KMS_REGION", "") or None)
    return _client


def _error_code(exc):
    response = getattr(exc, "response", None)
    return response.get("Error", {}).get("Code", "") if isinstance(response, dict) else ""


def _context(project_id):
    if project_id is None:
        raise ValueError("KMS 암호화에는 project_id가 필요합니다.")
    return {"project": str(project_id)}


def _b64(data):
    return base64.urlsafe_b64encode(data).decode()


def _unb64(text):
    return base64.urlsafe_b64decode(text.encode())


def _kms_encrypt(text, project_id):
    context = _context(project_id)
    try:
        res = _kms().generate_data_key(KeyId=kms_key_id(), KeySpec="AES_256", EncryptionContext=context)
    except Exception as exc:
        raise KeyUnavailable("암호화 키 서비스(KMS)를 부르지 못했습니다.") from exc
    nonce = os.urandom(12)
    sealed = AESGCM(res["Plaintext"]).encrypt(nonce, text.encode(), context["project"].encode())
    return f"{KMS_PREFIX}{_b64(res['CiphertextBlob'])}:{_b64(nonce + sealed)}"


def _kms_decrypt(token, project_id):
    context = _context(project_id)
    try:
        wrapped, body = token[len(KMS_PREFIX):].split(":", 1)
        wrapped, body = _unb64(wrapped), _unb64(body)
    except ValueError as exc:
        raise DecryptError("저장된 비밀번호 형식이 올바르지 않습니다. 테스트 계정을 다시 입력해주세요.") from exc
    try:
        res = _kms().decrypt(CiphertextBlob=wrapped, EncryptionContext=context)
    except Exception as exc:
        if _error_code(exc) in _BAD_KEY_CODES:
            raise DecryptError("저장된 비밀번호를 풀 수 없습니다. 테스트 계정을 다시 입력해주세요.") from exc
        raise KeyUnavailable("암호화 키 서비스(KMS)를 부르지 못했습니다.") from exc
    try:
        return AESGCM(res["Plaintext"]).decrypt(body[:12], body[12:], context["project"].encode()).decode()
    except InvalidTag as exc:
        raise DecryptError("저장된 비밀번호를 풀 수 없습니다. 테스트 계정을 다시 입력해주세요.") from exc


# ---------- 다른 코드가 쓰는 함수 ----------

def encrypt(text, project_id=None):
    if uses_kms():
        return _kms_encrypt(text, project_id)
    return _fernet().encrypt(text.encode()).decode()


def decrypt(token, project_id=None):
    if token.startswith(KMS_PREFIX):
        return _kms_decrypt(token, project_id)
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise DecryptError("저장된 비밀번호를 풀 수 없습니다. 테스트 계정을 다시 입력해주세요.") from exc


def needs_reencrypt(token):
    """KMS를 켰는데 아직 예전(Fernet) 방식으로 저장된 값인지."""
    return uses_kms() and not token.startswith(KMS_PREFIX)