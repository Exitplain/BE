"""로그인 업무 규칙: 이메일 코드 발급·확인, Google 로그인, 사용자·워크스페이스 준비."""
import hashlib
import hmac
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from common.exceptions import ApiError

from . import google
from .models import EmailLoginCode, ExternalIdentity, Workspace, WorkspaceMember


def _setting(name, default):
    return getattr(settings, name, default)


def code_ttl():
    return _setting("EXITPLAIN_LOGIN_CODE_TTL_SECONDS", 600)            # 코드 유효 10분


def resend_seconds():
    return _setting("EXITPLAIN_LOGIN_CODE_RESEND_SECONDS", 60)          # L03 '0:42 후 코드 재전송'


def max_attempts():
    return _setting("EXITPLAIN_LOGIN_CODE_MAX_ATTEMPTS", 5)


def normalize_email(email):
    return email.strip().lower()


def _hash(email, code):
    key = settings.SECRET_KEY.encode()
    return hmac.new(key, f"{email}:{code}".encode(), hashlib.sha256).hexdigest()


# ---------- 이메일 코드 (L02 → L03·L04·L05) ----------

def request_email_code(email):
    email = normalize_email(email)
    now = timezone.now()
    last = EmailLoginCode.objects.filter(email=email).first()
    if last:
        wait = resend_seconds() - (now - last.created_at).total_seconds()
        if wait > 0:
            raise ApiError(429, "RESEND_TOO_SOON", f"{int(wait) + 1}초 뒤에 코드를 다시 보낼 수 있어요.",
                           {"retry_after": [str(int(wait) + 1)]})
    code = f"{secrets.randbelow(10 ** 6):06d}"
    EmailLoginCode.objects.create(email=email, code_hash=_hash(email, code),
                                  expires_at=now + timedelta(seconds=code_ttl()))
    minutes = code_ttl() // 60
    send_mail(
        subject="[Exitplain] 로그인 코드",
        message=f"Exitplain 로그인 코드는 {code} 입니다.\n{minutes}분 안에 입력해 주세요.\n"
                "직접 요청하지 않았다면 이 메일을 무시해 주세요.",
        from_email=None, recipient_list=[email],
    )
    return {"email": email, "expires_in": code_ttl(), "resend_available_in": resend_seconds()}


def verify_email_code(email, code):
    """가장 최근 코드만 확인한다. 틀린 횟수는 오류를 내기 전에 커밋해야 한다
    (트랜잭션 안에서 오류를 내면 횟수 증가가 같이 되돌려져 무제한으로 맞혀 볼 수 있게 됨)."""
    email = normalize_email(email)
    with transaction.atomic():
        record = EmailLoginCode.objects.select_for_update().filter(email=email).first()
        if record is None or record.used_at is not None:
            raise ApiError(400, "CODE_NOT_REQUESTED", "코드를 먼저 요청해 주세요.")
        if record.is_expired:
            raise ApiError(400, "CODE_EXPIRED", "코드가 만료됐어요. 코드를 다시 받아 주세요.")
        if record.attempts >= max_attempts():
            raise ApiError(429, "TOO_MANY_ATTEMPTS", "입력 횟수를 넘었어요. 코드를 다시 받아 주세요.")
        record.attempts += 1
        matched = hmac.compare_digest(record.code_hash, _hash(email, code))
        if matched:
            record.used_at = timezone.now()
        record.save(update_fields=["attempts", "used_at"])
    if not matched:  # 트랜잭션 밖에서 오류 → 횟수 증가는 저장된 상태
        # L04 문구
        raise ApiError(400, "CODE_INVALID", "코드가 맞지 않습니다. 다시 한번 입력해 주세요",
                       {"remaining_attempts": [str(max_attempts() - record.attempts)]})
    return get_or_create_user(email)


# ---------- Google (L01 'Google로 계속하기') ----------

@transaction.atomic
def login_with_google(token):
    try:
        info = google.verifier()(token)
    except google.GoogleTokenError as exc:
        raise ApiError(400, "GOOGLE_TOKEN_INVALID", str(exc)) from exc
    identity = ExternalIdentity.objects.select_related("user").filter(
        provider=ExternalIdentity.Provider.GOOGLE, subject=info["sub"]).first()
    if identity:
        return identity.user
    # 같은 이메일로 이메일 코드 로그인을 한 적이 있으면 같은 계정에 연결한다 (Google이 이메일을 확인한 경우만)
    user = get_or_create_user(info["email"], name=info.get("name", ""))
    ExternalIdentity.objects.create(user=user, provider=ExternalIdentity.Provider.GOOGLE,
                                    subject=info["sub"], email=normalize_email(info["email"]))
    return user


# ---------- 사용자·워크스페이스 ----------

@transaction.atomic
def get_or_create_user(email, name=""):
    email = normalize_email(email)
    User = get_user_model()
    user, created = User.objects.get_or_create(username=email, defaults={"email": email, "first_name": name[:150]})
    if created:
        user.set_unusable_password()   # 비밀번호 로그인은 쓰지 않음
        user.save(update_fields=["password"])
    ensure_workspace(user)
    return user


def ensure_workspace(user):
    membership = WorkspaceMember.objects.select_related("workspace").filter(user=user).order_by("created_at").first()
    if membership:
        return membership.workspace
    base = user.first_name or user.email.split("@")[0]
    workspace = Workspace.objects.create(name=f"{base}의 워크스페이스"[:50], created_by=user)
    WorkspaceMember.objects.create(workspace=workspace, user=user, role=WorkspaceMember.Role.OWNER)
    return workspace


def current_membership(user):
    return WorkspaceMember.objects.select_related("workspace").filter(user=user).order_by("created_at").first()


def user_dict(user):
    return {"id": user.id, "email": user.email, "name": user.first_name}


def workspace_dict(membership):
    if membership is None:
        return None
    return {"id": str(membership.workspace.id), "name": membership.workspace.name, "role": membership.role}


def session_dict(request):
    """GET /auth/session과 로그인 응답에 공통으로 쓰는 모양."""
    if not request.user.is_authenticated:
        return {"authenticated": False}
    return {
        "authenticated": True,
        "user": user_dict(request.user),
        "workspace": workspace_dict(current_membership(request.user)),
        "session_expires_at": request.session.get_expiry_date().isoformat(),
    }