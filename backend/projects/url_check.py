"""5.0 URL 확인: 주소가 허용 대상인지, 실제로 접속되는지만 본다 (사이트 분석은 B의 Discovery).

순서: 허용 대상 검사(netguard, SSRF) → HTTP 요청(리다이렉트는 직접 따라가며 매번 다시 검사) → 결과 저장.
응답 본문은 읽지 않고, 내부 오류 문구는 사용자에게 그대로 내보내지 않는다.
알려진 한계: 검사 뒤 접속 사이에 DNS 답이 바뀌는 공격(DNS rebinding)은 막지 못한다 (runs/netguard.py 참고).
"""
import time
from urllib.parse import urljoin

import requests
from django.utils import timezone

from .models import Project

CONNECT_TIMEOUT = 3
READ_TIMEOUT = 5
MAX_REDIRECTS = 3
USER_AGENT = "ExitplainURLCheck/1.0"

MESSAGES = {
    "BLOCKED_TARGET": None,  # netguard 가 만든 문구를 그대로 쓴다
    "INVALID_URL": "HTTP 또는 HTTPS로 시작하는 주소를 입력해주세요",
    "DNS_FAILED": "주소를 찾을 수 없어요. 도메인을 다시 확인해주세요",
    "TIMEOUT": "응답이 너무 늦어요. 사이트가 켜져 있는지 확인해주세요",
    "TLS_ERROR": "보안 인증서에 문제가 있어 접속할 수 없어요",
    "CONNECTION_FAILED": "사이트에 접속할 수 없어요",
    "TOO_MANY_REDIRECTS": "주소가 너무 많이 다른 곳으로 넘어가요",
    "HTTP_ERROR": "사이트가 오류를 돌려줬어요",
}


class _Failed(Exception):
    def __init__(self, reason, message=None, http_status=None):
        self.reason, self.message, self.http_status = reason, message or MESSAGES[reason], http_status


def _fetch(url):
    """주소를 열어 (최종 HTTP 상태, 걸린 시간 ms)를 돌려준다. 실패하면 _Failed."""
    from runs import netguard  # projects 는 runs 보다 아래 계층이라 호출 시점에만 가져온다

    started = time.monotonic()
    for _ in range(MAX_REDIRECTS + 1):
        try:
            netguard.check_origin(url)
        except netguard.TargetNotAllowed as exc:
            raise _Failed("BLOCKED_TARGET", str(exc)) from None
        try:
            response = requests.get(url, stream=True, allow_redirects=False, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                                    headers={"User-Agent": USER_AGENT})
        except requests.exceptions.SSLError:
            raise _Failed("TLS_ERROR") from None
        except requests.exceptions.Timeout:
            raise _Failed("TIMEOUT") from None
        except requests.exceptions.InvalidURL:
            raise _Failed("INVALID_URL") from None
        except requests.exceptions.ConnectionError as exc:
            name_error = any(word in str(exc) for word in ("getaddrinfo", "Name or service not known", "NameResolution", "resolve"))
            raise _Failed("DNS_FAILED" if name_error else "CONNECTION_FAILED") from None
        except requests.exceptions.RequestException:
            raise _Failed("CONNECTION_FAILED") from None
        try:
            code, location = response.status_code, response.headers.get("Location")
        finally:
            response.close()
        if code in (301, 302, 303, 307, 308) and location:
            url = urljoin(url, location)
            continue
        elapsed = int((time.monotonic() - started) * 1000)
        if code >= 400:
            raise _Failed("HTTP_ERROR", f"사이트가 오류를 돌려줬어요 (HTTP {code})", http_status=code)
        return code, elapsed
    raise _Failed("TOO_MANY_REDIRECTS")


def check_project_url(project):
    """프로젝트 입구 주소를 확인하고 결과를 저장해 응답용 dict 로 돌려준다."""
    started = time.monotonic()
    reason = message = None
    try:
        http_status, elapsed_ms = _fetch(project.entry_url)
        status = Project.CheckStatus.OK
    except _Failed as failure:
        status, reason, message = Project.CheckStatus.FAILED, failure.reason, failure.message
        http_status, elapsed_ms = failure.http_status, int((time.monotonic() - started) * 1000)
    project.check_status, project.check_http = status, http_status
    project.check_ms, project.check_checked_at = elapsed_ms, timezone.now()
    project.save(update_fields=["check_status", "check_http", "check_ms", "check_checked_at"])
    return {"status": status, "http_status": http_status, "elapsed_ms": elapsed_ms,
            "checked_at": project.check_checked_at.isoformat(), "reason": reason, "message": message}
