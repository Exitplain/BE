"""실측 수집 (R01~R05 의 바탕): 키 발급, 수집 받기, 설치 확인, 경로별 집계.

받는 쪽 원칙
- 키는 공개 값이라 Origin 이 프로젝트 허용 출처와 같을 때만 받는다.
- 경로의 개인정보로 보이는 조각(이메일·긴 숫자·UUID·긴 16진수)은 ':id' 로 바꿔 저장한다.
- 잘못된 요청도 204 로 조용히 버린다 (사이트 쪽 콘솔에 오류를 남기지 않게). 버린 이유는 돌려주지 않는다.
"""
import json
import re
from datetime import timedelta
from urllib.parse import urlsplit

from django.conf import settings
from django.core.cache import cache
from django.db.models import Count
from django.utils import timezone

from common.projects import project_target

from .models import MeasureHit, MeasureSite

KEY_RE = re.compile(r"^ep_[0-9a-f]{12}$")
SID_RE = re.compile(r"^[a-z0-9]{8,40}$")
STEP_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
PRIVATE_SEGMENT = re.compile(
    r"@|^\d{4,}$|^[0-9a-f]{16,}$|^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
MAX_BODY = 2048
MAX_PATH = 300
INSTALLED_WINDOW = timedelta(days=7)


def per_minute_limit():
    return getattr(settings, "EXITPLAIN_MEASURE_PER_MINUTE", 600)


def origins_of(project):
    return {o.rstrip("/").lower() for o in project_target(project)["allowed_origins"]}


def normalize_path(raw):
    if not isinstance(raw, str) or not raw.startswith("/"):
        return None
    path = raw.split("?", 1)[0].split("#", 1)[0]
    parts = [":id" if PRIVATE_SEGMENT.search(p) else p for p in path.split("/")]
    path = "/".join(parts)
    if len(path) > 1:
        path = path.rstrip("/") or "/"
    return path[:MAX_PATH]


def parse(body):
    if not body or len(body) > MAX_BODY:
        return None
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    key, sid, step = data.get("key"), data.get("sid"), data.get("step") or ""
    path = normalize_path(data.get("path"))
    if not (isinstance(key, str) and KEY_RE.match(key) and isinstance(sid, str) and SID_RE.match(sid) and path):
        return None
    if step and not (isinstance(step, str) and STEP_RE.match(step)):
        return None
    return {"key": key, "sid": sid, "path": path, "step": step}


def _over_limit(site):
    bucket = f"measure:{site.pk}:{int(timezone.now().timestamp() // 60)}"
    cache.add(bucket, 0, 120)
    try:
        return cache.incr(bucket) > per_minute_limit()
    except ValueError:
        return False


def collect(body, origin):
    """받으면 True. 버리면 False (뷰는 둘 다 204)."""
    hit = parse(body)
    if hit is None:
        return False
    site = MeasureSite.objects.select_related("project").filter(key=hit["key"]).first()
    if site is None:
        return False
    origin = (origin or "").rstrip("/").lower()
    if origin not in origins_of(site.project) or _over_limit(site):
        return False
    MeasureHit.objects.create(site=site, sid=hit["sid"], path=hit["path"], step=hit["step"])
    MeasureSite.objects.filter(pk=site.pk).update(last_received_at=timezone.now(), last_origin=origin)
    return True


def site_for(project, create=False):
    if create:
        return MeasureSite.objects.get_or_create(project=project)[0]
    return MeasureSite.objects.filter(project=project).first()


def script_url(request):
    base = getattr(settings, "EXITPLAIN_PUBLIC_URL", "") or request.build_absolute_uri("/")
    return base.rstrip("/") + "/api/v1/e.js"


def snippet(request, site):
    return f'<script src="{script_url(request)}" data-project="{site.key}" async></script>'


def deployed(project):
    """실측은 실제 방문자가 오는 배포 주소가 있어야 한다. 미리보기(로컬) 주소만 있는 프로젝트는 대상이 아니다."""
    host = (urlsplit(project.entry_url or "").hostname or "").lower()
    return bool(host) and host not in {"localhost", "127.0.0.1", "::1"} and not host.endswith(".localhost")


def status_dict(request, project, site):
    now = timezone.now()
    out = {
        "project_id": project.pk,
        "available": deployed(project),
        "origins": sorted(origins_of(project)),
        "key": None, "snippet": None,
        "installed": False, "last_received_at": None, "last_origin": None,
        "sessions_24h": 0, "events_24h": 0,
    }
    if not out["available"]:
        out["reason"] = "NOT_DEPLOYED"
        out["message"] = "실측 분석은 실제 방문자가 오는 배포 주소가 있어야 해요."
    if site is None:
        return out
    recent = MeasureHit.objects.filter(site=site, created_at__gte=now - timedelta(hours=24))
    out.update({
        "key": site.key,
        "snippet": snippet(request, site),
        "installed": bool(site.last_received_at and site.last_received_at >= now - INSTALLED_WINDOW),
        "last_received_at": site.last_received_at.isoformat() if site.last_received_at else None,
        "last_origin": site.last_origin or None,
        "sessions_24h": recent.values("sid").distinct().count(),
        "events_24h": recent.count(),
    })
    return out


def steps(site, days):
    """최근 days 일 경로·단계별 고유 세션 수 (R05 매핑 화면의 '최근 7일 이벤트')."""
    since = timezone.now() - timedelta(days=days)
    rows = (MeasureHit.objects.filter(site=site, created_at__gte=since)
            .values("path", "step").annotate(sessions=Count("sid", distinct=True), events=Count("id"))
            .order_by("-sessions", "path", "step"))
    total = MeasureHit.objects.filter(site=site, created_at__gte=since).values("sid").distinct().count()
    return {
        "days": days,
        "total_sessions": total,
        "items": [{"step_id": r["step"] or r["path"], "path": r["path"], "step": r["step"] or None,
                   "sessions": r["sessions"], "events": r["events"]} for r in rows],
    }
