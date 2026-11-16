"""테스트 계정 저장·조회와 다른 앱이 쓰는 함수.

규칙 (Figma P04b·P04d)
- 계정은 최대 20개. 화면에서 직접 입력하든 붙여넣기('아이디,비밀번호' 한 줄에 하나)하든
  FE가 accounts 배열로 바꿔 보낸다. 서버는 배열 하나만 받는다.
- PUT은 목록 전체를 바꾼다. 보낸 순서가 곧 position(페르소나와 짝짓는 순서).
  기존 계정은 id를 같이 보내면 password를 생략해도 기존 비밀번호를 유지한다. 새 계정은 password 필수.
- 같은 아이디 두 번 금지. 응답에는 비밀번호를 절대 넣지 않는다.
- '테스트 계정 로그인 상태로 테스트'는 계정이 MIN_ACCOUNTS개 이상 있어야 시작할 수 있다
  (페르소나 세그먼트가 4개라 세그먼트마다 1명 이상).

보안 (LLM 담당자 피드백 반영)
- 감사 기록: 등록·수정·삭제·실행 사용·로그인 확인·자동 삭제를 TestAccountEvent에 남긴다. 비밀번호는 남기지 않는다.
- 보관 기간: 마지막으로 쓰이거나 바뀐 뒤 TEST_ACCOUNT_RETENTION_DAYS(기본 90일)가 지나면
  `manage.py purge_test_accounts`가 지운다 (서버에서 하루 한 번 실행).
"""
from datetime import timedelta
from urllib.parse import urlsplit

from django.conf import settings
from django.db import transaction
from django.db.models.functions import Coalesce
from django.utils import timezone

from common.exceptions import ApiError, validation_error
from common.projects import project_target

from . import crypto
from .models import TestAccount, TestAccountEvent, TestAccountSet

MAX_ACCOUNTS = 20
MIN_ACCOUNTS = 4
EVENT_LIMIT = 100


def retention_days():
    return int(getattr(settings, "TEST_ACCOUNT_RETENTION_DAYS", 90))


def _origin(url):
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}"


def _log(project, action, *, account=None, username="", actor=None, detail=""):
    TestAccountEvent.objects.create(project=project, actor=actor, action=action,
                                    account_id=account.pk if account else None,
                                    username=(account.username if account else username)[:200],
                                    detail=(detail or "")[:200])


def account_count(project):
    return TestAccount.objects.filter(project=project).count()


def _expires_at(account):
    last = account.last_used_at or account.updated_at
    return last + timedelta(days=retention_days())


def accounts_dict(project):
    setting = TestAccountSet.objects.filter(project=project).first()
    items = [{"id": str(a.id), "position": a.position, "username": a.username, "has_password": True,
              "status": a.status, "status_message": a.status_message,
              "checked_at": a.checked_at.isoformat() if a.checked_at else None,
              "last_used_at": a.last_used_at.isoformat() if a.last_used_at else None,
              "expires_at": _expires_at(a).isoformat()}
             for a in TestAccount.objects.filter(project=project).order_by("position")]
    return {"project_id": project.pk, "login_url": setting.login_url if setting else "",
            "count": len(items), "min": MIN_ACCOUNTS, "max": MAX_ACCOUNTS,
            "ready": len(items) >= MIN_ACCOUNTS, "retention_days": retention_days(), "items": items}


def _check_login_url(project, login_url):
    if not login_url:
        return ""
    p = urlsplit(login_url)
    if p.scheme not in {"http", "https"} or not p.netloc or p.username or p.password:
        raise validation_error("login_url", "http(s)로 시작하는 올바른 로그인 화면 주소를 입력해주세요.")
    if _origin(login_url) not in project_target(project)["allowed_origins"]:
        raise validation_error("login_url", "프로젝트에 등록한 사이트의 로그인 화면만 넣을 수 있어요.")
    return login_url


@transaction.atomic
def replace_accounts(project, user, login_url, accounts):
    login_url = _check_login_url(project, (login_url or "").strip())
    if len(accounts) > MAX_ACCOUNTS:
        raise validation_error("accounts", f"테스트 계정은 최대 {MAX_ACCOUNTS}개까지 넣을 수 있어요.")
    existing = {str(a.id): a for a in TestAccount.objects.select_for_update().filter(project=project)}
    seen, cleaned = set(), []
    for i, item in enumerate(accounts):
        username = item["username"].strip()
        if not username:
            raise validation_error(f"accounts.{i}.username", "아이디를 입력해주세요.")
        if username in seen:
            raise validation_error(f"accounts.{i}.username", f"같은 아이디가 두 번 들어 있어요: {username}")
        seen.add(username)
        old = existing.get(str(item["id"])) if item.get("id") else None
        if item.get("id") and old is None:
            raise validation_error(f"accounts.{i}.id", "이 프로젝트의 계정이 아닙니다.")
        password = item.get("password") or ""
        if not password and old is None:
            raise validation_error(f"accounts.{i}.password", "비밀번호를 입력해주세요.")
        cleaned.append((old, username, password))

    keep = {old.pk for old, _, _ in cleaned if old}
    original = {old.pk: old.username for old, _, _ in cleaned if old}
    for removed in TestAccount.objects.filter(project=project).exclude(pk__in=keep):
        _log(project, TestAccountEvent.Action.REMOVED, account=removed, actor=user)
        removed.delete()
    # 순서·아이디를 바꾸는 중에 유니크 제약이 부딪히지 않게 먼저 임시 값으로 비운다
    for old, _, _ in cleaned:
        if old:
            old.username = f"__tmp__{old.pk}"
            old.save(update_fields=["username"])
    for position, (old, username, password) in enumerate(cleaned, start=1):
        if old is None:
            created = TestAccount.objects.create(project=project, position=position, username=username,
                                                 password_encrypted=crypto.encrypt(password))
            _log(project, TestAccountEvent.Action.ADDED, account=created, actor=user)
            continue
        changes = []
        if original[old.pk] != username:
            changes.append(f"아이디 변경(이전: {original[old.pk]})")
        if password:
            changes.append("비밀번호 변경")
        old.position, old.username = position, username
        if password:
            old.password_encrypted = crypto.encrypt(password)
        if changes:                                   # 아이디·비밀번호가 바뀌면 다시 확인해야 한다
            old.status, old.status_message, old.checked_at = TestAccount.Status.UNCHECKED, "", None
        old.save()
        if changes:
            _log(project, TestAccountEvent.Action.UPDATED, account=old, actor=user, detail=", ".join(changes))
    TestAccountSet.objects.update_or_create(project=project, defaults={"login_url": login_url, "updated_by": user})
    return accounts_dict(project)


def events_dict(project, limit=EVENT_LIMIT):
    """감사 기록 (최근 순). 비밀번호는 원래 저장하지 않는다."""
    labels = dict(TestAccountEvent.Action.choices)
    events = TestAccountEvent.objects.filter(project=project).select_related("actor")[:limit]
    return {"project_id": project.pk, "items": [
        {"id": e.id, "action": e.action, "action_name": labels.get(e.action, e.action),
         "account_id": str(e.account_id) if e.account_id else None, "username": e.username,
         "actor": e.actor.email if e.actor else None, "detail": e.detail, "created_at": e.created_at.isoformat()}
        for e in events]}


# ---------- 다른 앱이 쓰는 함수 ----------

def ensure_ready(project):
    """사이트 확인(discoveries)에서 '테스트 계정 로그인 상태로 테스트'를 고를 때 부른다."""
    count = account_count(project)
    if count < MIN_ACCOUNTS:
        raise ApiError(409, "TEST_ACCOUNTS_REQUIRED",
                       f"테스트 계정을 {MIN_ACCOUNTS}개 이상 등록해주세요 · 지금 {count}개",
                       {"accounts": [f"지금 {count}개"]})
    return count


def snapshot_accounts(project, personas):
    """계획 확정(plans.confirm_plan)에서 부른다. 페르소나와 계정을 순서대로 짝지은 표 (비밀번호 없음)."""
    accounts = list(TestAccount.objects.filter(project=project).order_by("position"))
    if len(accounts) != len(personas):
        raise ApiError(409, "TEST_ACCOUNT_COUNT_MISMATCH", "테스트 계정 수와 페르소나 수가 달라요. 3단계에서 인원을 다시 맞춰주세요.")
    setting = TestAccountSet.objects.filter(project=project).first()
    return {"login_url": setting.login_url if setting else "",
            "pairs": [{"persona_code": p["code"], "account_id": str(a.id), "username": a.username}
                      for p, a in zip(personas, accounts)]}


def credentials_for_snapshot(snapshot, *, run_id=None):
    """실행할 때(백엔드 A·에이전트) 쓰는 계정 목록. 비밀번호를 풀어서 돌려준다.

    반환: [{"account_id", "persona_code", "username", "password", "login_url"}]
    account_id는 로그인 확인 결과를 record_check(account_id, ok, message)로 남길 때 쓴다.
    로그인 없이 테스트하는 계획이면 빈 목록. 확정 뒤 지워진 계정은 건너뛴다.
    부를 때마다 '실행에 사용' 감사 기록을 남기고 last_used_at을 갱신한다 (보관 기간이 다시 시작됨).
    파일·로그에 남기지 말고 에이전트 프로세스에만 넘길 것.
    """
    info = (snapshot.data or {}).get("test_accounts")
    if not info:
        return []
    by_id = {str(a.id): a for a in TestAccount.objects.select_related("project").filter(
        pk__in=[p["account_id"] for p in info["pairs"]])}
    now = timezone.now()
    result = []
    for pair in info["pairs"]:
        account = by_id.get(pair["account_id"])
        if account is None:
            continue
        result.append({"account_id": str(account.id), "persona_code": pair["persona_code"], "username": account.username,
                       "password": crypto.decrypt(account.password_encrypted), "login_url": info["login_url"]})
        TestAccount.objects.filter(pk=account.pk).update(last_used_at=now)   # updated_at은 건드리지 않는다
        _log(account.project, TestAccountEvent.Action.USED, account=account,
             detail=f"run {run_id}" if run_id else "")
    return result


def record_check(account_id, ok, message=""):
    """로그인 확인 결과 기록 (P04c 확인됨/실패). 에이전트 쪽 로그인 기능이 생기면 여기에 결과를 넣는다."""
    account = TestAccount.objects.select_related("project").filter(pk=account_id).first()
    if account is None:
        return
    TestAccount.objects.filter(pk=account.pk).update(
        status=TestAccount.Status.OK if ok else TestAccount.Status.FAILED,
        status_message=(message or "")[:200], checked_at=timezone.now())
    _log(account.project, TestAccountEvent.Action.CHECKED, account=account,
         detail=("성공" if ok else f"실패: {message}")[:200])


# ---------- 보관 기간 지나면 자동 삭제 ----------

def expired_accounts(now=None):
    """마지막으로 쓰이거나(last_used_at) 바뀐(updated_at) 뒤 보관 기간이 지난 계정."""
    cutoff = (now or timezone.now()) - timedelta(days=retention_days())
    return (TestAccount.objects.select_related("project")
            .annotate(last_activity=Coalesce("last_used_at", "updated_at"))
            .filter(last_activity__lt=cutoff))


@transaction.atomic
def purge_expired(now=None, dry_run=False):
    """지운(또는 dry_run이면 지울) 계정 수를 돌려준다."""
    accounts = list(expired_accounts(now))
    if dry_run:
        return len(accounts)
    for account in accounts:
        _log(account.project, TestAccountEvent.Action.EXPIRED, account=account,
             detail=f"{retention_days()}일 동안 사용·수정 없음")
        account.delete()
    return len(accounts)