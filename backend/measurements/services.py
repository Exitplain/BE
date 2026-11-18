"""실측 수집 (R01~R05): 키 발급, 수집 받기, 설치 확인, 경로별 집계, 단계 매핑.

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

from common.exceptions import ApiError
from common.projects import project_target
from reports.journey import screen_id
from reports.models import ReportSnapshot

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
    path = "/".join(":id" if PRIVATE_SEGMENT.search(p) else p for p in path.split("/"))
    # 에이전트 실행 퍼널과 같은 단계 ID 규칙 (소문자, 상품 12번·34번 → /product/:id). 그래야 매핑을 자동으로 맞출 수 있다
    return screen_id(path)[:MAX_PATH]


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


# ---------- R05 단계 매핑: 실측 경로(단계 ID) → 사전 테스트(예측) 실행 퍼널 단계 ----------

MAPPING_DAYS = 7


def latest_report(project):
    """이 프로젝트의 가장 최근 예측 리포트 (실행 퍼널 단계의 기준)."""
    return (ReportSnapshot.objects.filter(plan_snapshot__plan__project=project)
            .order_by("-created_at", "-revision").first())


def prediction_stages(report):
    """리포트의 과업별 실행 퍼널 단계를 하나로 모은다. 같은 화면 경로는 한 단계 (과업 이름을 함께 남긴다)."""
    stages, seen = [], {}
    for task in (report.data.get("tasks") or []) if report else []:
        for stage in (task.get("journey_funnel") or {}).get("stages", []):
            sid = stage["path"]
            if sid not in seen:
                seen[sid] = {"id": sid, "name": stage.get("name") or sid, "is_goal": bool(stage.get("is_goal")),
                             "tasks": []}
                stages.append(seen[sid])
            seen[sid]["tasks"].append(task.get("name") or task.get("task_id"))
    return stages


def _loose(path):
    """이름 유사 비교용: 끝의 s(복수형)·하이픈·밑줄 차이를 무시한다 (/products ≈ /product)."""
    return "/".join(p.replace("-", "").replace("_", "").rstrip("s") for p in path.split("/"))


def suggest(step_id, stage_ids):
    if step_id in stage_ids:
        return step_id, "exact"
    loose = {_loose(s): s for s in stage_ids}
    if step_id.startswith("/") and _loose(step_id) in loose:
        return loose[_loose(step_id)], "similar"
    return None, None


def mapping_dict(project, site):
    report = latest_report(project)
    stages = prediction_stages(report)
    stage_ids = [s["id"] for s in stages]
    counts = {r["step_id"]: r for r in steps(site, MAPPING_DAYS)["items"]}
    saved = site.mapping or {}
    rows = []
    for step_id in list(counts) + [k for k in saved if k not in counts]:
        suggested, why = suggest(step_id, stage_ids)
        has_saved = step_id in saved
        stage_id = saved[step_id] if has_saved else suggested
        if stage_id is not None and stage_id not in stage_ids:
            stage_id = None                                   # 새 리포트에서 사라진 단계
        rows.append({"step_id": step_id, "sessions": counts.get(step_id, {}).get("sessions", 0),
                     "events": counts.get(step_id, {}).get("events", 0),
                     "stage_id": stage_id, "suggested_stage_id": suggested, "suggestion": why, "saved": has_saved})
    out = {"project_id": project.pk, "days": MAPPING_DAYS,
           "report_id": str(report.id) if report else None, "stages": stages, "rows": rows,
           "mapped": sum(1 for r in rows if r["stage_id"]), "saved_at": site.mapping_saved_at.isoformat()
           if site.mapping_saved_at else None}
    if report is None:
        out.update({"reason": "NO_PREDICTION", "message": "먼저 사전 테스트를 한 번 실행해야 비교할 단계가 생겨요."})
    return out


def save_mapping(project, site, mapping):
    """{step_id: stage_id | null}. 없는 단계 ID 는 422. 보낸 행만 바꾸지 않고 통째로 바꾼다."""
    if not isinstance(mapping, dict) or len(mapping) > 500:
        raise ApiError(422, "VALIDATION_ERROR", "mapping 은 {단계 ID: 퍼널 단계 ID} 객체여야 합니다.",
                       {"mapping": ["객체여야 합니다."]})
    stage_ids = {s["id"] for s in prediction_stages(latest_report(project))}
    bad = sorted(k for k, v in mapping.items()
                 if not isinstance(k, str) or not k or len(k) > MAX_PATH or (v is not None and v not in stage_ids))
    if bad:
        raise ApiError(422, "VALIDATION_ERROR", "알 수 없는 퍼널 단계가 있습니다.",
                       {f"mapping.{k}": ["알 수 없는 퍼널 단계입니다."] for k in bad[:20]})
    site.mapping = dict(mapping)
    site.mapping_saved_at = timezone.now()
    site.save(update_fields=["mapping", "mapping_saved_at"])
    return mapping_dict(project, site)
