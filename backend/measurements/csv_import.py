"""실측 CSV 올리기: 수집 스크립트(e.js)를 심을 수 없는 사이트가 자기 로그를 CSV 로 내보내 올린다.

한 줄 = 방문자 1명의 화면 이동 1건 (e.js 가 보내는 것과 같은 단위).

    session_id,timestamp,path,step,device
    a81f3c,2026-11-18 14:03:22,/,,desktop
    a81f3c,2026-11-18 14:03:40,/product/12,,desktop
    a81f3c,2026-11-18 14:04:05,/cart,cart,desktop

- session_id (필수): 같은 방문을 묶는 값. 무엇이든 되지만 그대로 저장하지 않고 해시한다 (회원 ID·이메일이어도 남지 않음).
- timestamp (필수): ISO 8601(2026-11-18T14:03:22+09:00), '2026-11-18 14:03:22', 유닉스 초·밀리초. 시간대가 없으면 한국 시간.
- path (필수): /cart 같은 경로 또는 전체 주소. 쿼리·# 뒤는 버리고, 숫자·이메일 같은 조각은 :id 로 바꾼다 (e.js 와 같은 규칙).
- step (선택): exitplain.step 과 같은 단계 이름 (영문·숫자·-·_).
- device (선택): mobile | desktop. 없으면 screen_width(선택, 숫자)로 나눈다.

분석 기간이 최대 30일이라 30일보다 오래된 줄·미래 시각 줄은 넣지 않고 건너뛴 수로 알려준다.
같은 파일을 다시 올리면 409. 올린 것은 통째로 지울 수 있다 (잘못 올린 경우).
"""
import csv
import hashlib
import io
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

from common.exceptions import ApiError

from .models import MeasureHit, MeasureImport
from .services import STEP_RE, device_of, normalize_path

MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 100_000
MAX_ERRORS = 20
WINDOW_DAYS = 30
KST = ZoneInfo("Asia/Seoul")
REQUIRED = ("session_id", "timestamp", "path")
OPTIONAL = ("step", "device", "screen_width")

TEMPLATE_ROWS = [
    ("a81f3c", "2026-11-18 14:03:22", "/", "", "desktop"),
    ("a81f3c", "2026-11-18 14:03:40", "/product/12", "", "desktop"),
    ("a81f3c", "2026-11-18 14:04:05", "/cart", "cart", "desktop"),
    ("b27d90", "2026-11-18T15:10:00+09:00", "https://shop.example.com/", "", "mobile"),
    ("b27d90", "2026-11-18T15:10:31+09:00", "https://shop.example.com/product/7?ref=ad", "", "mobile"),
]


def template_csv():
    """예시 CSV (엑셀에서 한글이 깨지지 않게 BOM)."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["session_id", "timestamp", "path", "step", "device"])
    writer.writerows(TEMPLATE_ROWS)
    return "﻿" + out.getvalue()


def _decode(raw):
    for encoding in ("utf-8-sig", "cp949"):        # 엑셀 '다른 이름으로 저장'의 CSV 는 cp949 인 경우가 많다
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ApiError(422, "VALIDATION_ERROR", "CSV 를 읽지 못했어요. UTF-8 로 저장해 주세요.", {"file": ["인코딩"]})


def _timestamp(value):
    value = value.strip()
    if value.isdigit():
        number = int(value)
        seconds = number / 1000 if number > 10_000_000_000 else number   # 밀리초
        return datetime.fromtimestamp(seconds, tz=dt_timezone.utc)
    when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return when if when.tzinfo else when.replace(tzinfo=KST)


def _path(value):
    value = value.strip()
    if value.lower().startswith(("http://", "https://")):
        value = urlsplit(value).path or "/"
    return normalize_path(value)


def _sid(site, raw):
    """방문 id 는 저장하지 않는다. 사이트 키로 해시해 다른 프로젝트와 섞이지 않게 한다 (SID_RE 형식)."""
    return "c" + hashlib.sha256(f"{site.key}:{raw}".encode()).hexdigest()[:31]


def _device(row):
    device = (row.get("device") or "").strip().lower()
    if device in {"mobile", "desktop"}:
        return device
    if device:
        raise ValueError("device 는 mobile 또는 desktop 이어야 해요")
    width = (row.get("screen_width") or "").strip()
    return device_of(int(width)) if width.isdigit() else ""


def _parse_row(site, row):
    raw_sid = (row.get("session_id") or "").strip()
    if not raw_sid or len(raw_sid) > 200:
        raise ValueError("session_id 가 비었거나 너무 길어요")
    try:
        when = _timestamp(row.get("timestamp") or "")
    except (ValueError, OverflowError, OSError):
        raise ValueError("timestamp 형식을 읽지 못했어요 (예: 2026-11-18 14:03:22)") from None
    path = _path(row.get("path") or "")
    if not path:
        raise ValueError("path 는 /로 시작하는 경로나 전체 주소여야 해요")
    step = (row.get("step") or "").strip()
    if step and not STEP_RE.match(step):
        raise ValueError("step 은 영문·숫자·-·_ 64자까지예요")
    return {"sid": _sid(site, raw_sid), "path": path, "step": step, "device": _device(row), "created_at": when}


def import_csv(site, user, upload):
    """CSV 파일 → MeasureHit. 줄마다 검사해 맞는 줄만 넣고, 틀린 줄은 앞의 20개까지 줄 번호와 이유를 돌려준다."""
    if upload is None:
        raise ApiError(422, "VALIDATION_ERROR", "CSV 파일을 올려 주세요.", {"file": ["필수"]})
    if upload.size > MAX_BYTES:
        raise ApiError(413, "FILE_TOO_LARGE", "CSV 는 5MB까지 올릴 수 있어요. 기간을 나눠 올려 주세요.")
    raw = upload.read()
    digest = hashlib.sha256(raw).hexdigest()
    if MeasureImport.objects.filter(site=site, sha256=digest).exists():
        raise ApiError(409, "DUPLICATE_IMPORT", "이미 올린 파일이에요.")
    reader = csv.DictReader(io.StringIO(_decode(raw)))
    reader.fieldnames = [(f or "").strip().lower() for f in reader.fieldnames or []]
    missing = [c for c in REQUIRED if c not in reader.fieldnames]
    if missing:
        raise ApiError(422, "VALIDATION_ERROR", f"필수 열이 없어요: {', '.join(missing)}",
                       {"file": [f"필수 열: {', '.join(REQUIRED)}"]})

    now = timezone.now()
    oldest = now - timedelta(days=WINDOW_DAYS)
    hits, seen, errors = [], set(), []
    total = skipped_old = skipped_dup = 0
    for line, row in enumerate(reader, start=2):              # 1번째 줄은 머리글
        total += 1
        if total > MAX_ROWS:
            raise ApiError(413, "FILE_TOO_LARGE", f"CSV 는 {MAX_ROWS:,}줄까지 올릴 수 있어요. 기간을 나눠 올려 주세요.")
        try:
            hit = _parse_row(site, row)
        except ValueError as exc:
            if len(errors) < MAX_ERRORS:
                errors.append({"line": line, "message": str(exc)})
            continue
        if not oldest <= hit["created_at"] <= now + timedelta(minutes=5):
            skipped_old += 1
            continue
        key = (hit["sid"], hit["path"], hit["step"], hit["created_at"])
        if key in seen:
            skipped_dup += 1
            continue
        seen.add(key)
        hits.append(hit)
    invalid = total - len(hits) - skipped_old - skipped_dup
    if not hits:
        raise ApiError(422, "NO_VALID_ROWS", "넣을 수 있는 줄이 없어요. 형식과 최근 30일 안의 기록인지 확인해 주세요.",
                       {"rows": [f"{e['line']}번째 줄: {e['message']}" for e in errors] or ["최근 30일 안의 줄이 없어요"]})

    times = [h["created_at"] for h in hits]
    with transaction.atomic():
        record = MeasureImport.objects.create(
            site=site, created_by=user if getattr(user, "is_authenticated", False) else None,
            filename=(getattr(upload, "name", "") or "")[:200], sha256=digest, rows_total=total,
            rows_imported=len(hits), sessions=len({h["sid"] for h in hits}), first_at=min(times), last_at=max(times))
        MeasureHit.objects.bulk_create([MeasureHit(site=site, source_import=record, **h) for h in hits], batch_size=2000)
    return {**import_dict(record), "skipped": {"invalid": invalid, "outside_window": skipped_old,
                                               "duplicate": skipped_dup}, "errors": errors}


def import_dict(record):
    return {"id": record.pk, "filename": record.filename, "rows_total": record.rows_total,
            "rows_imported": record.rows_imported, "sessions": record.sessions,
            "first_at": record.first_at.isoformat() if record.first_at else None,
            "last_at": record.last_at.isoformat() if record.last_at else None,
            "created_at": record.created_at.isoformat()}


def list_imports(site):
    return [import_dict(r) for r in MeasureImport.objects.filter(site=site).order_by("-created_at")]


def delete_import(site, import_id):
    record = MeasureImport.objects.filter(site=site, pk=import_id).first()
    if record is None:
        raise ApiError(404, "NOT_FOUND", "올린 파일을 찾을 수 없어요.")
    record.delete()                                    # 그 파일로 넣은 줄도 함께 (CASCADE)
