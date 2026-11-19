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
    """프로젝트마다 가장 최근 사전 테스트(Run) 요약과 실측·보정 상태를 한 번의 쿼리에 붙인다.

    calibrations·measurements 는 projects 를 참조하는 쪽이라, runs 와 같은 이유로 import 하지 않고
    apps.get_model 로 호출 시점에만 가져온다.
    """
    Run = _run_model()
    latest = Run.objects.filter(project=OuterRef("pk"), kind="pretest").order_by("-sequence_no")
    revision = apps.get_model("calibrations", "CalibrationRevision").objects.filter(
        project=OuterRef("pk")).order_by("-revision")
    site = apps.get_model("measurements", "MeasureSite").objects.filter(project=OuterRef("pk"))
    return queryset.annotate(
        lp_id=Subquery(latest.values("id")[:1]),
        lp_no=Subquery(latest.values("sequence_no")[:1]),
        lp_status=Subquery(latest.values("status")[:1]),
        lp_created=Subquery(latest.values("created_at")[:1]),
        lp_finished=Subquery(latest.values("finished_at")[:1]),
        cal_rev=Subquery(revision.values("revision")[:1]),
        cal_restored=Subquery(revision.values("restored_from")[:1]),
        cal_at=Subquery(revision.values("created_at")[:1]),
        cal_basis=Subquery(revision.values("basis")[:1]),
        ms_id=Subquery(site.values("id")[:1]),
        ms_last=Subquery(site.values("last_received_at")[:1]),
    )


def _iso(value):
    return value.isoformat() if value else None


def _last_pretest(project):
    if getattr(project, "lp_id", None) is None:
        return None
    return {"run_id": str(project.lp_id), "sequence_no": project.lp_no, "status": project.lp_status,
            "created_at": _iso(project.lp_created), "finished_at": _iso(project.lp_finished)}


def _calibration(project):
    """가장 최근 보정 리비전. 없거나 기본 모형으로 되돌렸으면(restored_from=0) 보정 전이다.

    mean_error_pp 는 보정을 만들 때 기준으로 삼은 실측 대비 평균 오차(%p)다. 사람 관측만으로 만들었거나
    예전 값으로 되돌린 리비전은 기준 오차가 없어서 None 이다.
    """
    rev = getattr(project, "cal_rev", None)
    if rev is None or getattr(project, "cal_restored", None) == 0:
        return {"status": "before", "revision": None, "applied_at": None, "mean_error_pp": None}
    basis = project.cal_basis
    if isinstance(basis, str):          # 일부 DB 는 JSON 하위 쿼리를 문자열로 돌려준다
        try:
            basis = json.loads(basis)
        except ValueError:
            basis = None
    mean_error = basis.get("mean_error") if isinstance(basis, dict) else None
    return {"status": "applied", "revision": rev, "applied_at": _iso(project.cal_at),
            "mean_error_pp": mean_error if isinstance(mean_error, (int, float)) else None}


def _measurement(project):
    """실측 연결 상태. 수집 키가 없으면 미연결, 키는 있는데 아직 받은 기록이 없으면 대기, 받았으면 연결됨."""
    if getattr(project, "ms_id", None) is None:
        return {"status": "unconnected", "last_received_at": None}
    last = project.ms_last
    return {"status": "connected" if last else "waiting", "last_received_at": _iso(last)}


def _domain(url):
    return urlsplit(url).netloc


def project_summary(project):
    """목록(C02)의 한 줄. 대시보드의 실측·보정 열도 여기서 함께 내려준다."""
    return {"id": project.pk, "name": project.name, "entry_url": project.entry_url, "domain": _domain(project.entry_url),
            "is_sample": project.is_sample, "archived_at": _iso(project.archived_at),
            "last_pretest": _last_pretest(project), "measurement": _measurement(project),
            "calibration": _calibration(project), "updated_at": project.updated_at.isoformat()}


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
        "measurement": _measurement(annotated), "calibration": _calibration(annotated),
        "created_at": project.created_at.isoformat(), "updated_at": project.updated_at.isoformat(),
    }


# ---------- 삭제(보관)·되살리기 ----------
#
# 삭제는 보관(archived_at)으로 한다. 실행·리포트·화면 캡처·실측·보정 기록은 지우지 않아 되살릴 수 있다.
# 목록·사이드바·샘플 목록은 보관한 프로젝트를 이미 빼고 보여 준다. 같은 주소로 새 프로젝트도 다시 만들 수 있다.
# 보관하면 참여자 응답 링크를 닫고, 실측 수집도 받지 않는다 (measurements.services.collect).

def archive_project(project):
    from django.utils import timezone

    from common.exceptions import ApiError
    if project.is_sample:
        raise ApiError(403, "FORBIDDEN", "샘플 프로젝트는 삭제할 수 없어요.")
    Run = _run_model()
    if Run.objects.filter(project=project, status__in=Run.ACTIVE_STATUSES).exists():
        raise ApiError(409, "RUN_IN_PROGRESS", "진행 중인 테스트가 있어요. 끝나거나 중지한 뒤에 삭제해 주세요.")
    if project.archived_at is None:
        now = timezone.now()
        project.archived_at = now
        project.save(update_fields=["archived_at", "updated_at"])
        from calibrations.models import ParticipantLink
        ParticipantLink.objects.filter(project=project, disabled_at__isnull=True).update(disabled_at=now)
    return project


def restore_project(project):
    from common.exceptions import ApiError
    if project.archived_at is None:
        return project
    if Project.objects.filter(owner=project.owner, archived_at__isnull=True, entry_url=project.entry_url) \
            .exclude(pk=project.pk).exists():
        raise ApiError(409, "DUPLICATE_PROJECT", "같은 주소의 프로젝트가 이미 있어요. 그 프로젝트를 먼저 삭제해 주세요.",
                       {"entry_url": ["같은 주소의 프로젝트가 있어요."]})
    project.archived_at = None
    project.save(update_fields=["archived_at", "updated_at"])
    return project
