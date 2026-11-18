"""사이트 확인(P02·P03). 둘러보기는 에이전트(agent/explore.py·agent/preflight.py)가 하고, 여기서는 검사·진행 기록·결과 저장을 맡는다.

두 가지로 시작할 수 있다.
- create_discovery: 요청 안에서 끝까지 기다렸다가 결과를 돌려준다 (기존 동작, 201).
- start_discovery: running 상태로 만들어 바로 돌려주고(202) 백그라운드 스레드에서 둘러본다. 화면은 GET 으로 진행 피드를 읽는다.

둘러보는 쪽은 이벤트 루프 안이라 ORM 을 쓸 수 없다. 진행 기록·백그라운드 실행은 common/progress.py 를 쓴다.
"""
import asyncio
import logging
import time
from datetime import timedelta
from urllib.parse import urlsplit

from django.conf import settings
from django.utils import timezone

from common.exceptions import ApiError
from common.progress import (HEARTBEAT_SECONDS, PROGRESS_LIMIT, STALE_SECONDS, ProgressLog,  # noqa: F401
                             next_seq, run_tracked, start_background)
from common.projects import project_target

from .models import Discovery

logger = logging.getLogger(__name__)

UNREACHABLE = "사이트를 살펴보지 못했습니다. 잠시 뒤 다시 확인해주세요."


def run_discovery(variant):
    """에이전트의 읽기 전용 탐색 (agent/preflight.py). 테스트에서는 mock한다.

    로컬 데모 쇼핑몰을 띄워 시작 화면 링크만 읽는다. 입력 URL을 직접 읽지는 않는다.
    """
    from agent.preflight import discover_site
    return asyncio.run(discover_site(variant))


def run_exploration(entry_url, allowed_origins, login=None, progress=None):
    """처음 보는 URL을 읽기 전용으로 둘러보고 기능·과업 후보를 받는다 (agent/explore.py). 테스트에서는 mock한다.

    login: {"url", "username", "password"} 이면 그 계정으로 로그인한 상태로 둘러본다.
    progress: 진행 사건을 받는 함수. 이벤트 루프 안에서 불리므로 ORM 을 쓰면 안 된다.
    """
    from agent.explore import discover_url
    return asyncio.run(discover_url(entry_url, list(allowed_origins), login=login, progress=progress))


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


class PreviewUnavailable(Exception):
    pass


def _prepare_source(project, data):
    """코드(정적 사이트)로 확인: 받은 스냅샷을 미리보기 주소로 둘러본다. 데모·URL 설정과 관계없이 explore 로 돈다."""
    from sources.models import SourceSnapshot
    from sources.preview import origin_for
    from sources.services import previewable
    snapshot = SourceSnapshot.objects.select_related("connection").filter(
        pk=data["source_snapshot_id"], connection__project=project).first()
    if snapshot is None:
        raise ApiError(422, "VALIDATION_ERROR", "이 프로젝트에서 받은 코드가 아닙니다.",
                       {"source_snapshot_id": ["이 프로젝트의 코드 스냅샷이 아닙니다."]})
    if not previewable(snapshot):
        raise ApiError(409, "SOURCE_NOT_RUNNABLE", "URL 없이 열 수 있는 정적 사이트 코드가 아니에요. 빌드 결과물 zip 이나 배포 URL 을 써 주세요.")
    if data["login_mode"] != Discovery.LoginMode.NONE:
        raise ApiError(422, "VALIDATION_ERROR", "코드로 테스트할 때는 로그인 연결을 쓸 수 없어요.",
                       {"login_mode": ["none 만 쓸 수 있습니다."]})
    origin = origin_for(snapshot.pk)
    return {"entry_url": origin + "/", "allowed_origins": [origin], "explore": True, "account": None,
            "variant": data["variant"], "login_mode": data["login_mode"], "source": snapshot}


def _prepare(project, data):
    """요청 검사. 둘러보기에 필요한 값을 돌려준다 (테스트 계정 값은 메모리에만 둔다)."""
    if data.get("source_snapshot_id"):
        return _prepare_source(project, data)
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
    return {"entry_url": entry_url, "allowed_origins": target["allowed_origins"], "explore": explore,
            "account": account, "variant": data["variant"], "login_mode": data["login_mode"], "source": None}


def _work(ctx, progress):
    """실제 둘러보기. 별도 스레드에서 돈다."""
    if ctx["source"] is not None:
        from sources.preview import reachable
        progress({"kind": "preview", "message": "받은 코드를 미리보기로 여는 중이에요"})
        if not reachable(ctx["allowed_origins"][0]):
            raise PreviewUnavailable()
    if ctx["explore"]:
        account = ctx["account"]
        login = {k: account[k] for k in ("url", "username", "password")} if account else None
        return run_exploration(ctx["entry_url"], ctx["allowed_origins"], login=login, progress=progress)
    progress({"kind": "demo_started", "message": "데모 쇼핑몰의 시작 화면을 확인하는 중이에요"})
    result = run_discovery(ctx["variant"])
    progress({"kind": "demo_finished", "message": f"기능 {len(result.get('features') or [])}개를 찾았어요"})
    return result


def _failure(exc, account):
    """둘러보기 예외 → (HTTP 상태, 오류 코드, 사용자 문구). 내부 예외 문구는 내보내지 않는다."""
    if isinstance(exc, PreviewUnavailable):
        return 503, "PREVIEW_UNAVAILABLE", "받은 코드를 여는 미리보기 서버가 꺼져 있어요. 관리자에게 알려주세요."
    if account and isinstance(exc, ValueError) and "로그인" in str(exc):
        from testaccounts.services import record_check
        record_check(account["account_id"], False, "사이트 확인 중 로그인 실패")
        return 409, "TEST_ACCOUNT_LOGIN_FAILED", "테스트 계정으로 로그인하지 못했습니다. 계정과 로그인 화면 주소를 확인해주세요."
    return 503, "SITE_UNREACHABLE", UNREACHABLE


def _new(user, project, ctx):
    return Discovery.objects.create(
        project=project, created_by=user, entry_url=ctx["entry_url"], login_mode=ctx["login_mode"],
        source_snapshot=ctx["source"],
        variant="explore" if ctx["explore"] else ctx["variant"], status=Discovery.Status.RUNNING,
        progress=[{"seq": 1, "t": 0.0, "kind": "queued", "message": "사이트 확인을 시작했어요"}],
        expires_at=timezone.now() + timedelta(seconds=settings.EXITPLAIN_DISCOVERY_TTL_SECONDS))


def _execute(discovery, ctx):
    """둘러보기를 끝까지 돌리고 결과를 저장한다. 실패하면 (HTTP 상태, 코드, 문구), 성공하면 None."""
    result, error, log = run_tracked(discovery, lambda progress: _work(ctx, progress), f"discovery-{discovery.pk}")
    account, failure = ctx["account"], None
    if error is not None:
        logger.warning("사이트 확인 실패 %s: %s", discovery.pk, type(error).__name__)
        failure = _failure(error, account)
    else:
        if account:
            from testaccounts.services import record_check
            record_check(account["account_id"], True, "사이트 확인 중 로그인 성공")
        if not result.get("features"):
            failure = 422, "NO_FEATURES_FOUND", "시작 화면에서 확인할 수 있는 기능을 찾지 못했습니다."

    discovery.elapsed_seconds = round(time.monotonic() - log.started, 2)
    if failure:
        _, code, message = failure
        log.add({"kind": "failed", "message": message})
        log.take()
        discovery.status, discovery.error_code, discovery.error_message = Discovery.Status.FAILED, code, message
        discovery.save()
        return failure
    log.add({"kind": "succeeded", "message": "사이트 확인을 마쳤어요"})
    log.take()
    discovery.status = Discovery.Status.SUCCEEDED
    discovery.features, discovery.tasks = result["features"], result.get("tasks", [])
    discovery.screen_names = _screen_names(result.get("screen_names"))
    discovery.note = (result.get("note") or "")[:300]
    discovery.expires_at = timezone.now() + timedelta(seconds=settings.EXITPLAIN_DISCOVERY_TTL_SECONDS)
    discovery.save()
    return None


def create_discovery(user, project, data):
    """요청 안에서 끝까지 둘러보고 결과를 돌려준다. 실패하면 ApiError (실패 기록은 남는다)."""
    ctx = _prepare(project, data)
    discovery = _new(user, project, ctx)
    failure = _execute(discovery, ctx)
    if failure:
        raise ApiError(*failure)
    return discovery


def _active_count():
    cutoff = timezone.now() - timedelta(seconds=STALE_SECONDS)
    return Discovery.objects.filter(status=Discovery.Status.RUNNING, updated_at__gte=cutoff).count()


def _run_in_background(discovery_id, ctx):
    try:
        _execute(Discovery.objects.get(pk=discovery_id), ctx)
    except Exception:
        Discovery.objects.filter(pk=discovery_id, status=Discovery.Status.RUNNING).update(
            status=Discovery.Status.FAILED, error_code="SITE_UNREACHABLE", error_message=UNREACHABLE,
            updated_at=timezone.now())
        raise


def start_discovery(user, project, data):
    """running 으로 만들어 바로 돌려주고 백그라운드에서 둘러본다.

    EXITPLAIN_DISCOVERY_BACKGROUND=False 면 같은 요청 안에서 끝까지 돌린다 (테스트용). 응답 모양은 같다.
    """
    ctx = _prepare(project, data)
    if _active_count() >= settings.EXITPLAIN_DISCOVERY_MAX_ACTIVE:
        raise ApiError(429, "DISCOVERY_BUSY", "지금 확인 중인 사이트가 많아요. 잠시 뒤 다시 시도해주세요.")
    discovery = _new(user, project, ctx)
    if settings.EXITPLAIN_DISCOVERY_BACKGROUND:
        start_background(_run_in_background, discovery.pk, ctx, name=f"discovery-job-{discovery.pk}")
    else:
        _execute(discovery, ctx)
    return discovery


def mark_if_stale(discovery):
    """running 인데 STALE_SECONDS 동안 갱신이 없으면(서버 재시작 등) 실패로 바꾼다."""
    if discovery.status == Discovery.Status.RUNNING and \
            timezone.now() - discovery.updated_at > timedelta(seconds=STALE_SECONDS):
        message = "사이트 확인이 중간에 멈췄어요. 다시 시도해주세요."
        discovery.status, discovery.error_code, discovery.error_message = \
            Discovery.Status.FAILED, "DISCOVERY_INTERRUPTED", message
        discovery.progress = (discovery.progress + [{"seq": next_seq(discovery.progress), "t": None, "kind": "failed",
                                                      "message": message}])[-PROGRESS_LIMIT:]
        discovery.save(update_fields=["status", "error_code", "error_message", "progress", "updated_at"])
    return discovery
