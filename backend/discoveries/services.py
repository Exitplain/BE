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
        # Figma P04 로그인 연결은 아직 제품 API가 없다
        raise ApiError(409, "LOGIN_CONNECTION_UNAVAILABLE",
                       "테스트 계정 로그인 연결은 아직 지원하지 않습니다. '로그인 없이 테스트'로 진행해주세요.")

    started = time.monotonic()
    try:
        result = run_discovery(data["variant"])
    except Exception as exc:
        raise ApiError(503, "SITE_UNREACHABLE", "사이트를 살펴보지 못했습니다. 잠시 뒤 다시 확인해주세요.") from exc
    if not result.get("features"):
        raise ApiError(422, "NO_FEATURES_FOUND", "시작 화면에서 확인할 수 있는 기능을 찾지 못했습니다.")

    now = timezone.now()
    return Discovery.objects.create(
        project=project, created_by=user, entry_url=entry_url, login_mode=data["login_mode"],
        variant=data["variant"], features=result["features"], tasks=result.get("tasks", []),
        note=(result.get("note") or "")[:300], elapsed_seconds=round(time.monotonic() - started, 2),
        expires_at=now + timedelta(seconds=settings.EXITPLAIN_DISCOVERY_TTL_SECONDS),
    )