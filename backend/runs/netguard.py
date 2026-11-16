"""테스트 대상 주소의 SSRF 방어. 실행 생성과 URL 확인(5.0)이 함께 쓴다.

실행은 우리 서버의 Chromium 이 사용자가 입력한 주소를 연다. 내부 주소를 막지 않으면 서버 안쪽 자원
(특히 클라우드 인스턴스 메타데이터 169.254.169.254)이 스크린샷으로 사용자에게 노출될 수 있다.

규칙
- 클라우드 메타데이터 주소(169.254.0.0/16, fd00:ec2::/32)는 어떤 설정으로도 허용하지 않는다.
- 공인 주소가 아닌 것(사설·루프백·링크 로컬·CGNAT·예약 대역)은 settings.ALLOWED_TARGET_ORIGINS 에 적힌 origin 만 허용한다.
- 호스트 이름은 DNS 로 풀어서 나온 모든 IP 를 검사한다. 풀리지 않는 이름은 접속할 수 없으므로 통과시킨다.
알려진 한계: DNS 를 검사한 뒤 접속하기 전에 답이 바뀌는 공격(DNS rebinding)은 막지 못한다. 에이전트가 허용 origin 밖 이동을 막는다.
"""
import ipaddress
import socket
from urllib.parse import urlsplit

from django.conf import settings

METADATA_NETWORKS = [ipaddress.ip_network("169.254.0.0/16"), ipaddress.ip_network("fd00:ec2::/32")]


class TargetNotAllowed(ValueError):
    pass


def resolve_host(host, port):
    """호스트의 모든 IP. 이름이 풀리지 않으면 빈 목록. (테스트에서 바꿔 끼울 수 있게 분리)"""
    try:
        return sorted({info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})
    except (socket.gaierror, UnicodeError, OSError):
        return []


def _normalize(origin):
    parts = urlsplit(origin.strip())
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}"


def _ip(value):
    ip = ipaddress.ip_address(value.split("%")[0])
    return ip.ipv4_mapped if getattr(ip, "ipv4_mapped", None) else ip


def check_origin(origin):
    """허용되지 않는 대상이면 TargetNotAllowed. 통과하면 None."""
    parts = urlsplit(origin)
    host = parts.hostname
    if parts.scheme not in {"http", "https"} or not host:
        raise TargetNotAllowed("HTTP 또는 HTTPS로 시작하는 주소를 입력해주세요")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        addresses = [_ip(host)]
    except ValueError:
        addresses = [_ip(a) for a in resolve_host(host, port)]
    allowed = {_normalize(o) for o in settings.ALLOWED_TARGET_ORIGINS}
    for ip in addresses:
        if any(ip in net for net in METADATA_NETWORKS):
            raise TargetNotAllowed("클라우드 메타데이터 주소는 테스트 대상으로 사용할 수 없어요")
        if not ip.is_global and _normalize(origin) not in allowed:
            raise TargetNotAllowed("내부 네트워크 주소는 테스트 대상으로 사용할 수 없어요")
