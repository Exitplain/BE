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
    return {"key": key, "sid": sid, "path": path, "step": step, "device": device_of(data.get("w"))}


MOBILE_MAX_WIDTH = 767


def device_of(width):
    """화면 너비만 받아 모바일·데스크톱으로 나눈다 (기기 이름·user agent 는 받지 않는다)."""
    if isinstance(width, bool) or not isinstance(width, int) or not 0 < width <= 10000:
        return ""
    return "mobile" if width <= MOBILE_MAX_WIDTH else "desktop"


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
    MeasureHit.objects.create(site=site, sid=hit["sid"], path=hit["path"], step=hit["step"], device=hit["device"])
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


# ---------- R06 실측 퍼널 · R08 예측 vs 실측 ----------
#
# 과업마다 예측 리포트의 실행 퍼널 단계를 그대로 쓰고, 매핑으로 그 단계에 연결된 실측 단계(경로)에 온 고유 세션을 센다.
# - 실측 도달률 = 그 단계에 온 세션 ÷ 첫 단계에 온 세션 (순서를 엄격히 따지지 않는다: 예측 rate_of_valid 와 같은 방식)
# - 예측 범위 = 예측 도달 세션/유효 세션 비율의 Wilson 95% 구간 (페르소나 수가 적을수록 넓어진다)
# - 오차 = 실측 − 범위 중앙값, 실측이 범위 밖이면 차이 구간
# - 매핑이 없는 단계는 실측 null (평균 오차·차이 구간에서 뺀다)

MIN_SAMPLE = 100          # 첫 단계 세션이 이보다 적으면 "수집 중" (비교는 보여주되 표본 부족으로 표시)
DEVICES = ("all", "mobile", "desktop")
Z95 = 1.96


def wilson(successes, total):
    if not total:
        return None
    p = successes / total
    denom = 1 + Z95 ** 2 / total
    centre = (p + Z95 ** 2 / (2 * total)) / denom
    half = Z95 * ((p * (1 - p) / total + Z95 ** 2 / (4 * total ** 2)) ** 0.5) / denom
    return max(0.0, centre - half) * 100, min(1.0, centre + half) * 100


def _pct(value):
    return None if value is None else round(value, 1)


def _stage_sessions(site, days, device, mapping):
    """{예측 단계 ID: 그 단계로 매핑된 실측 단계에 온 고유 sid 집합}"""
    since = timezone.now() - timedelta(days=days)
    hits = MeasureHit.objects.filter(site=site, created_at__gte=since)
    if device != "all":
        hits = hits.filter(device=device)
    by_stage = {}
    for path, step, sid in hits.values_list("path", "step", "sid").distinct():
        stage = mapping.get(step or path)
        if stage:
            by_stage.setdefault(stage, set()).add(sid)
    return by_stage


def _max_drop(stages, key):
    """도달률이 가장 크게 떨어지는 이웃 단계 쌍 (from, to). 값이 없는 단계는 건너뛴다."""
    points = [(s["id"], s["name"], key(s)) for s in stages if key(s) is not None]
    best = None
    for (a_id, a_name, a), (b_id, b_name, b) in zip(points, points[1:]):
        if best is None or a - b > best[0]:
            best = (a - b, {"from": a_id, "to": b_id, "label": f"{a_name}→{b_name}"})
    return best[1] if best else None


def _task_compare(task, by_stage):
    funnel = task.get("journey_funnel") or {}
    valid = funnel.get("valid_sessions") or 0
    stages = funnel.get("stages") or []
    base = len(by_stage.get(stages[0]["path"], ())) if stages else 0
    rows = []
    for stage in stages:
        band = wilson(stage.get("reached") or 0, valid)
        sessions = len(by_stage[stage["path"]]) if stage["path"] in by_stage else None
        actual = sessions / base * 100 if sessions is not None and base else None
        low, high = (band if band else (None, None))
        mid = (low + high) / 2 if band else None
        error = actual - mid if actual is not None and mid is not None else None
        rows.append({
            "id": stage["path"], "name": stage.get("name") or stage["path"], "is_goal": bool(stage.get("is_goal")),
            "predicted": {"low": _pct(low), "high": _pct(high), "mid": _pct(mid),
                          "reached": stage.get("reached") or 0, "valid": valid},
            "actual": None if sessions is None else {"sessions": sessions, "rate": _pct(actual)},
            "error": _pct(error),
            "gap": actual is not None and band is not None and not (low <= actual <= high),
            "short_sample": sessions is not None and sessions < MIN_SAMPLE,
        })
    errors = [abs(r["error"]) for r in rows if r["error"] is not None]
    predicted_drop = _max_drop(rows, lambda r: r["predicted"]["mid"])
    actual_drop = _max_drop(rows, lambda r: r["actual"]["rate"] if r["actual"] else None)
    return {
        "task_id": task.get("task_id"), "name": task.get("name") or task.get("task_id"),
        "base_sessions": base, "collecting": base < MIN_SAMPLE,
        "stages": rows,
        "mapped_stages": sum(1 for r in rows if r["actual"] is not None),
        "mean_error": _pct(sum(errors) / len(errors)) if errors else None,
        "gaps": [r["id"] for r in rows if r["gap"]],
        "max_drop": {"predicted": predicted_drop, "actual": actual_drop,
                     "matched": bool(predicted_drop and actual_drop
                                     and (predicted_drop["from"], predicted_drop["to"])
                                     == (actual_drop["from"], actual_drop["to"]))},
    }


def funnel_dict(project, site, days, device):
    report = latest_report(project)
    stage_ids = {s["id"] for s in prediction_stages(report)}
    mapping = {}
    for row in mapping_dict(project, site)["rows"] if report else []:
        if row["stage_id"] in stage_ids:
            mapping[row["step_id"]] = row["stage_id"]
    by_stage = _stage_sessions(site, days, device, mapping) if mapping else {}
    until = timezone.now()
    out = {
        "project_id": project.pk, "days": days, "device": device,
        "since": (until - timedelta(days=days)).isoformat(), "until": until.isoformat(),
        "min_sample": MIN_SAMPLE,
        "report": None if report is None else {
            "report_id": str(report.id), "created_at": report.created_at.isoformat(),
            "run_sequence": report.run.sequence_no if report.run_id else None},
        "mapping_saved": bool(site.mapping_saved_at),
        "tasks": [_task_compare(t, by_stage) for t in (report.data.get("tasks") or [])] if report else [],
    }
    if report is None:
        out.update({"reason": "NO_PREDICTION", "message": "먼저 사전 테스트를 한 번 실행해야 비교할 단계가 생겨요."})
    return out
