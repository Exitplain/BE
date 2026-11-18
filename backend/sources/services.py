"""코드 받기(SRC-01·02)와 규칙 분석(SRC-03). 받기·분석은 백그라운드에서 돌고 진행 피드를 남긴다.

코드는 읽기만 한다. 서버에서 install·build·실행하지 않는다 (SRC-07 의 격리 실행 환경 전).
"""
import logging
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from common.exceptions import ApiError
from common.progress import STALE_SECONDS, next_seq, run_tracked, start_background

from . import analysis, intake, preview
from .models import SourceConnection, SourceSnapshot

logger = logging.getLogger(__name__)

FAILED = "코드를 받거나 읽지 못했어요. 잠시 뒤 다시 시도해주세요."
SKIP_LABELS = {"secret_like": "비밀값으로 보이는 파일", "excluded_dir": "빌드·의존성 폴더", "too_large": "너무 큰 파일",
               "unsafe_path": "위험한 경로", "symlink": "심볼릭 링크"}
RUNNABLE_TEXT = {"static": "정적 사이트라 URL 없이도 바로 열 수 있어요", "needs_build": "빌드가 필요해 지금은 코드 분석만 해요",
                 "unknown": "실행 방법을 판단하지 못해 코드 분석만 해요"}


def source_root():
    return Path(settings.EXITPLAIN_SOURCE_ROOT)


def tree_dir(snapshot_id):
    return source_root() / str(snapshot_id)


def _delete_tree(snapshot_id):
    path = tree_dir(snapshot_id)
    if path.exists():
        intake.rmtree(path)


def _incoming(snapshot_id):
    return source_root() / "_incoming" / f"{snapshot_id}.zip"


def _active_count():
    cutoff = timezone.now() - timedelta(seconds=STALE_SECONDS)
    return SourceSnapshot.objects.filter(state=SourceSnapshot.State.RUNNING, updated_at__gte=cutoff).count()


def _check_busy():
    if _active_count() >= settings.EXITPLAIN_SOURCE_MAX_ACTIVE:
        raise ApiError(429, "SOURCE_BUSY", "지금 받고 있는 코드가 많아요. 잠시 뒤 다시 시도해주세요.")


def _invalid(exc):
    return ApiError(422, exc.code, exc.message)


def _connection(user, project, provider, url=""):
    found = SourceConnection.objects.filter(project=project, provider=provider, repository_url=url,
                                            status=SourceConnection.Status.ACTIVE).first()
    return found or SourceConnection.objects.create(project=project, created_by=user, provider=provider, repository_url=url)


def _new_snapshot(user, connection, ref="", subdirectory=""):
    return SourceSnapshot.objects.create(
        connection=connection, created_by=user, ref=ref, subdirectory=subdirectory,
        progress=[{"seq": 1, "t": 0.0, "kind": "queued", "message": "코드 받기를 시작했어요"}])


def _start(snapshot, job):
    if settings.EXITPLAIN_SOURCE_BACKGROUND:
        start_background(_run_in_background, snapshot.pk, job, name=f"source-job-{snapshot.pk}")
    else:
        _execute(snapshot, job)
        snapshot.refresh_from_db()
    return snapshot


def create_from_zip(user, project, upload, subdirectory=""):
    """zip 업로드 → running 스냅샷 (202). 업로드 파일은 받은 뒤 지운다."""
    if upload is None:
        raise ApiError(422, "VALIDATION_ERROR", "zip 파일을 올려주세요.", {"file": ["파일이 없습니다."]})
    if upload.size > settings.EXITPLAIN_SOURCE_MAX_UPLOAD_BYTES:
        mb = settings.EXITPLAIN_SOURCE_MAX_UPLOAD_BYTES // (1024 * 1024)
        raise ApiError(413, "ZIP_TOO_LARGE", f"zip 파일은 {mb}MB 까지 올릴 수 있어요.")
    if not str(upload.name or "").lower().endswith(".zip"):
        raise ApiError(422, "ZIP_INVALID", "zip 파일만 올릴 수 있어요.", {"file": ["zip 파일이 아닙니다."]})
    try:
        subdirectory = intake.validate_subdirectory(subdirectory)
    except intake.IntakeError as exc:
        raise _invalid(exc) from None
    _check_busy()
    snapshot = _new_snapshot(user, _connection(user, project, SourceConnection.Provider.ZIP), subdirectory=subdirectory)
    path = _incoming(snapshot.pk)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as out:
        for chunk in upload.chunks():
            out.write(chunk)
    return _start(snapshot, {"kind": "zip", "zip": str(path)})


def create_from_git(user, project, url, ref="", subdirectory=""):
    """공개 https 저장소 → running 스냅샷 (202)."""
    try:
        url = intake.validate_git_url(url)
        ref = intake.validate_ref(ref)
        subdirectory = intake.validate_subdirectory(subdirectory)
    except intake.IntakeError as exc:
        raise _invalid(exc) from None
    _check_busy()
    snapshot = _new_snapshot(user, _connection(user, project, SourceConnection.Provider.GIT, url), ref, subdirectory)
    return _start(snapshot, {"kind": "git", "url": url, "ref": ref})


def _work(snapshot_id, subdirectory, job, progress):
    """디스크 작업만 한다 (ORM 없음). 별도 스레드에서 돈다."""
    dest = tree_dir(snapshot_id)
    dest.mkdir(parents=True, exist_ok=True)
    result = {"commit_sha": ""}
    if job["kind"] == "zip":
        progress({"kind": "unpacking", "message": "zip 파일을 여는 중이에요"})
        stats = intake.extract_zip(job["zip"], dest)
    else:
        progress({"kind": "cloning", "message": f"저장소를 받는 중이에요{' (' + job['ref'] + ')' if job['ref'] else ''}"})
        result["commit_sha"] = intake.clone(job["url"], job["ref"], dest)
        progress({"kind": "pinned", "message": f"commit {result['commit_sha'][:7]} 으로 고정했어요"})
        stats = intake.prune_tree(dest)
    skipped = {k: v for k, v in stats["skipped"].items() if v}
    note = ", ".join(f"{SKIP_LABELS[k]} {v}개" for k, v in skipped.items())
    progress({"kind": "received", "message": f"파일 {stats['files']}개를 받았어요" + (f" · 저장하지 않음: {note}" if note else "")})
    base = dest / subdirectory if subdirectory else dest
    if not base.is_dir():
        raise intake.IntakeError("SUBDIRECTORY_NOT_FOUND", f"'{subdirectory}' 폴더를 찾지 못했어요.")
    result.update(tree_digest=intake.tree_digest(dest), file_count=stats["files"], total_bytes=stats["bytes"], skipped=skipped)
    progress({"kind": "analyzing", "message": "코드 구조를 읽는 중이에요"})
    found = analysis.analyze(base)
    fw = found["framework"]
    if fw["status"] == "ok":
        progress({"kind": "framework", "message": f"{fw['framework']} · {fw['router']} 구조로 읽었어요"})
    else:
        progress({"kind": "framework", "message": f"프레임워크를 확정하지 못했어요 ({fw['reason']})"})
    counts = found["analysis"]["counts"]
    progress({"kind": "routes", "message": f"화면 경로 {counts['routes']}개, 화면 이동 {counts['edges'] + counts['global_edges']}개를 찾았어요"})
    progress({"kind": "runnable", "message": RUNNABLE_TEXT[found["runnable"]]})
    return {**result, **found}


def _execute(snapshot, job):
    result, error, log = run_tracked(snapshot, lambda progress: _work(snapshot.pk, snapshot.subdirectory, job, progress),
                                     f"source-{snapshot.pk}")
    if job["kind"] == "zip":
        Path(job["zip"]).unlink(missing_ok=True)
    if error is not None:
        if isinstance(error, intake.IntakeError):
            code, message = error.code, error.message
        else:
            logger.warning("코드 받기 실패 %s: %s", snapshot.pk, type(error).__name__)
            code, message = "SOURCE_FAILED", FAILED
        _delete_tree(snapshot.pk)
        log.add({"kind": "failed", "message": message})
        log.take()
        snapshot.state, snapshot.error_code, snapshot.error_message = SourceSnapshot.State.FAILED, code, message
        snapshot.files_deleted_at = timezone.now()
        snapshot.save()
        return
    log.add({"kind": "ready", "message": "코드를 준비했어요"})
    log.take()
    for key in ("commit_sha", "tree_digest", "file_count", "total_bytes", "skipped", "runnable", "static_root", "framework", "analysis"):
        setattr(snapshot, key, result[key])
    if snapshot.runnable == SourceSnapshot.Runnable.STATIC:
        # 미리보기 서버가 내보낼 폴더 (tree_digest 를 낸 뒤에 써서 내용 해시에 섞이지 않는다)
        preview.write_manifest(tree_dir(snapshot.pk), "/".join(p for p in (snapshot.subdirectory, snapshot.static_root) if p))
    snapshot.state = SourceSnapshot.State.READY
    snapshot.save()


def previewable(snapshot):
    """URL 없이 미리보기로 사이트 확인·실행할 수 있는 스냅샷인지."""
    return (snapshot.state == SourceSnapshot.State.READY and snapshot.runnable == SourceSnapshot.Runnable.STATIC
            and snapshot.files_deleted_at is None and snapshot.connection.status == SourceConnection.Status.ACTIVE)


def _run_in_background(snapshot_id, job):
    try:
        _execute(SourceSnapshot.objects.get(pk=snapshot_id), job)
    except Exception:
        SourceSnapshot.objects.filter(pk=snapshot_id, state=SourceSnapshot.State.RUNNING).update(
            state=SourceSnapshot.State.FAILED, error_code="SOURCE_FAILED", error_message=FAILED, updated_at=timezone.now())
        raise


def mark_if_stale(snapshot):
    """running 인데 STALE_SECONDS 동안 갱신이 없으면(서버 재시작 등) 실패로 바꾼다."""
    if snapshot.state == SourceSnapshot.State.RUNNING and \
            timezone.now() - snapshot.updated_at > timedelta(seconds=STALE_SECONDS):
        message = "코드 받기가 중간에 멈췄어요. 다시 시도해주세요."
        snapshot.state, snapshot.error_code, snapshot.error_message = SourceSnapshot.State.FAILED, "SOURCE_INTERRUPTED", message
        snapshot.progress = snapshot.progress + [{"seq": next_seq(snapshot.progress), "t": None, "kind": "failed", "message": message}]
        snapshot.save(update_fields=["state", "error_code", "error_message", "progress", "updated_at"])
    return snapshot


def revoke(connection):
    """연결 해제 (SRC-11): 새로 받지 않고, 받아 둔 파일을 지운다. 스냅샷 기록(commit·분석 요약)은 남긴다."""
    now = timezone.now()
    for snapshot in connection.snapshots.filter(files_deleted_at__isnull=True):
        _delete_tree(snapshot.pk)
        snapshot.files_deleted_at = now
        snapshot.save(update_fields=["files_deleted_at", "updated_at"])
    connection.status, connection.revoked_at = SourceConnection.Status.REVOKED, now
    connection.save(update_fields=["status", "revoked_at"])
