import asyncio
import time
from datetime import timedelta
from urllib.parse import urlsplit

from django.conf import settings
from django.utils import timezone

from common.exceptions import ApiError
from common.projects import project_target

from .models import Discovery


def run_discovery(variant):
    """에이전트의 읽기 전용 탐색 (agent/preflight.py). 테스트에서는 mock한다.

    로컬 데모 쇼핑몰을 띄워 시작 화면 링크만 읽는다. 입력 URL을 직접 읽지는 않는다.
    """
    from agent.preflight import discover_site
    return asyncio.run(discover_site(variant))


def run_exploration(entry_url, allowed_origins, login=None):
    """처음 보는 URL을 읽기 전용으로 둘러보고 기능·과업 후보를 받는다 (agent/explore.py). 테스트에서는 mock한다.

    login: {"url", "username", "password"} 이면 그 계정으로 로그인한 상태로 둘러본다.
    """
    from agent.explore import discover_url
    return asyncio.run(discover_url(entry_url, list(allowed_origins), login=login))


def _screen_names(names):
    """사이트 확인이 붙인 화면 역할 이름을 실행 퍼널과 같은 단계 ID(정규화 경로)로 맞춘다."""
    from reports.journey import screen_id
    result = {}
    for path, name in (names or {}).items():
        if isinstance(path, str) and isinstance(name, str) and name.strip():
            result.setdefault(screen_id(path), name.strip()[:20])
    return result


def _origin(url):
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}"


def create_discovery(user, project, data):
    target = project_target(project)
    entry_url = data.get("entry_url") or target["entry_url"]
    p = urlsplit(entry_url)
    if p.scheme not in {"http", "https"} or p.username or p.password:
        raise ApiError(422, "VALIDATION_ERROR", "http(s)로 시작하는 올바른 URL을 입력해주세요.",
                       {"entry_url": ["올바른 URL이 아닙니다."]})
    # 허용 목록·사설 IP 차단 같은 URL 검사는 백엔드 A 담당. 여기서는 프로젝트 출처 안인지만 본다.
    if _origin(entry_url) not in target["allowed_origins"]:
        raise ApiError(422, "VALIDATION_ERROR", "프로젝트에 등록한 사이트 주소만 테스트할 수 있습니다.",
                       {"entry_url": ["프로젝트 허용 주소가 아닙니다."]})
    if data["login_mode"] == Discovery.LoginMode.TEST_ACCOUNT:
        # Figma P04: 테스트 계정이 있어야 시작할 수 있다 (계정 1개 = 페르소나 1명)
        from testaccounts.services import ensure_ready
        ensure_ready(project)

    explore = getattr(settings, "EXITPLAIN_DISCOVERY_MODE", "demo") == "explore"
    account = None
    if explore and data["login_mode"] == Discovery.LoginMode.TEST_ACCOUNT:
        from testaccounts.services import credentials_for_discovery
        account = credentials_for_discovery(project)
        if account is None:
            raise ApiError(422, "LOGIN_URL_REQUIRED", "테스트 계정의 로그인 화면 주소를 먼저 등록해주세요.",
                           {"login_url": ["로그인 화면 주소가 없습니다."]})

    started = time.monotonic()
    try:
        if explore:
            login = {k: account[k] for k in ("url", "username", "password")} if account else None
            result = run_exploration(entry_url, target["allowed_origins"], login=login)
        else:
            result = run_discovery(data["variant"])
    except ValueError as exc:
        if account and "로그인" in str(exc):
            from testaccounts.services import record_check
            record_check(account["account_id"], False, "사이트 확인 중 로그인 실패")
            raise ApiError(409, "TEST_ACCOUNT_LOGIN_FAILED", "테스트 계정으로 로그인하지 못했습니다. 계정과 로그인 화면 주소를 확인해주세요.") from exc
        raise ApiError(503, "SITE_UNREACHABLE", "사이트를 살펴보지 못했습니다. 잠시 뒤 다시 확인해주세요.") from exc
    except Exception as exc:
        raise ApiError(503, "SITE_UNREACHABLE", "사이트를 살펴보지 못했습니다. 잠시 뒤 다시 확인해주세요.") from exc
    if account:
        from testaccounts.services import record_check
        record_check(account["account_id"], True, "사이트 확인 중 로그인 성공")
    if not result.get("features"):
        raise ApiError(422, "NO_FEATURES_FOUND", "시작 화면에서 확인할 수 있는 기능을 찾지 못했습니다.")

    now = timezone.now()
    return Discovery.objects.create(
        project=project, created_by=user, entry_url=entry_url, login_mode=data["login_mode"],
        screen_names=_screen_names(result.get("screen_names")),
        variant="explore" if explore else data["variant"], features=result["features"], tasks=result.get("tasks", []),
        note=(result.get("note") or "")[:300], elapsed_seconds=round(time.monotonic() - started, 2),
        expires_at=now + timedelta(seconds=settings.EXITPLAIN_DISCOVERY_TTL_SECONDS),
    )