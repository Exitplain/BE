"""프로젝트 API의 업무 규칙 (명세서 4. 프로젝트: C02 목록·검색, C03~C04 등록, C05 개요).

앱 의존 방향은 runs → plans → projects 다. 이 파일이 runs 모델을 import 하면 순환이 되므로,
실행 요약은 `apps.get_model("runs", "Run")` 으로 호출 시점에만 가져온다 (import 시점 의존 없음).
"""
import base64
import binascii
import json
import re
from urllib.parse import urlsplit, urlunsplit

from django.apps import apps
from django.db.models import Count, OuterRef, Q, Subquery
from django.utils.dateparse import parse_datetime

from common.exceptions import ApiError, validation_error

from .models import Project

URL_MESSAGE = "HTTP 또는 HTTPS로 시작하는 주소를 입력해주세요"
DUPLICATE_MESSAGE = "이미 같은 주소의 프로젝트가 있어요"
DEFAULT_PORTS = {"http": 80, "https": 443}


def _run_model():
    return apps.get_model("runs", "Run")


# ---------- URL 정규화 ----------

def normalize_entry_url(raw):
    """http(s) 주소만 받아 같은 사이트가 같은 문자열이 되게 정리한다.

    스킴·호스트는 소문자, 기본 포트·#fragment 제거, 경로가 없으면 '/'.
    사설 IP·SSRF 검사는 여기서 하지 않는다 (5.0 URL 확인에서 다룬다).
    """
    text = (raw or "").strip()
    if not text or re.search(r"\s", text):
        raise validation_error("entry_url", URL_MESSAGE)
    parts = urlsplit(text)
    scheme = parts.scheme.lower()
    try:
        port = parts.port
    except ValueError:
        raise validation_error("entry_url", URL_MESSAGE) from None
    if scheme not in DEFAULT_PORTS or not parts.hostname or parts.username or parts.password:
        raise validation_error("entry_url", URL_MESSAGE)
    host = parts.hostname.lower()
    if ":" in host:  # IPv6
        host = f"[{host}]"
    netloc = host if port in (None, DEFAULT_PORTS[scheme]) else f"{host}:{port}"
    return urlunsplit((scheme, netloc, parts.path or "/", parts.query, ""))


def origin_of(url):
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


# ---------- 등록 ----------

def create_project(user, *, name, entry_url, journey_note=""):
    url = normalize_entry_url(entry_url)
    if Project.objects.filter(owner=user, archived_at__isnull=True, entry_url=url).exists():
        raise ApiError(409, "DUPLICATE_PROJECT", DUPLICATE_MESSAGE, {"entry_url": [DUPLICATE_MESSAGE]})
    return Project.objects.create(owner=user, name=name, entry_url=url, allowed_origins=[origin_of(url)],
                                  journey_note=journey_note)


# ---------- 목록·검색 (cursor 페이지) ----------

def _encode_cursor(project):
    raw = json.dumps([project.updated_at.isoformat(), project.pk])
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode_cursor(cursor):
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        stamp, pk = json.loads(base64.urlsafe_b64decode(padded.encode()))
        updated_at = parse_datetime(stamp)
        if updated_at is None or not isinstance(pk, int):
            raise ValueError
        return updated_at, pk
    except (ValueError, TypeError, binascii.Error):
        raise ApiError(422, "VALIDATION_ERROR", "cursor가 올바르지 않습니다.",
                       {"cursor": ["cursor가 올바르지 않습니다."]}) from None


def _with_last_pretest(queryset):
    """프로젝트마다 가장 최근 사전 테스트(Run) 요약을 한 번의 쿼리에 붙인다."""
    Run = _run_model()
    latest = Run.objects.filter(project=OuterRef("pk"), kind="pretest").order_by("-sequence_no")
    return queryset.annotate(
        lp_id=Subquery(latest.values("id")[:1]),
        lp_no=Subquery(latest.values("sequence_no")[:1]),
        lp_status=Subquery(latest.values("status")[:1]),
        lp_created=Subquery(latest.values("created_at")[:1]),
        lp_finished=Subquery(latest.values("finished_at")[:1]),
    )


def _iso(value):
    return value.isoformat() if value else None


def _last_pretest(project):
    if getattr(project, "lp_id", None) is None:
        return None
    return {"run_id": str(project.lp_id), "sequence_no": project.lp_no, "status": project.lp_status,
            "created_at": _iso(project.lp_created), "finished_at": _iso(project.lp_finished)}


def _domain(url):
    return urlsplit(url).netloc


def project_summary(project):
    """목록(C02)의 한 줄. 실측·보정 열은 컷 범위라 넣지 않는다."""
    return {"id": project.pk, "name": project.name, "entry_url": project.entry_url, "domain": _domain(project.entry_url),
            "is_sample": project.is_sample, "archived_at": _iso(project.archived_at),
            "last_pretest": _last_pretest(project), "updated_at": project.updated_at.isoformat()}


def list_projects(user, *, q="", archived=False, limit=20, cursor=""):
    queryset = Project.objects.filter(owner=user, archived_at__isnull=not archived)
    if q.strip():
        queryset = queryset.filter(Q(name__icontains=q.strip()) | Q(entry_url__icontains=q.strip()))
    total = queryset.count()
    if cursor:
        updated_at, pk = _decode_cursor(cursor)
        queryset = queryset.filter(Q(updated_at__lt=updated_at) | Q(updated_at=updated_at, pk__lt=pk))
    rows = list(_with_last_pretest(queryset.order_by("-updated_at", "-pk"))[: limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {"items": [project_summary(p) for p in rows], "next_cursor": _encode_cursor(rows[-1]) if has_more else None,
            "total": total}


# ---------- 개요 (C05) ----------

def project_detail(project):
    Run = _run_model()
    counts = Run.objects.filter(project=project).aggregate(
        total=Count("id"), active=Count("id", filter=Q(status__in=Run.ACTIVE_STATUSES)))
    annotated = _with_last_pretest(Project.objects.filter(pk=project.pk)).get()
    return {
        "id": project.pk, "name": project.name, "entry_url": project.entry_url, "domain": _domain(project.entry_url),
        "allowed_origins": list(project.allowed_origins or []), "login_mode": project.login_mode,
        "journey_note": project.journey_note, "is_sample": project.is_sample, "archived_at": _iso(project.archived_at),
        "check": {"status": project.check_status, "http_status": project.check_http, "elapsed_ms": project.check_ms,
                  "checked_at": _iso(project.check_checked_at)},
        "runs": {"total": counts["total"], "active": counts["active"], "last_pretest": _last_pretest(annotated)},
        "created_at": project.created_at.isoformat(), "updated_at": project.updated_at.isoformat(),
    }
