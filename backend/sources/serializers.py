def snapshot_dict(s):
    """서버 경로는 내보내지 않는다. 라우트·이동의 file:line 은 받은 코드 기준 상대 경로."""
    c = s.connection
    return {
        "id": str(s.id), "project_id": c.project_id, "connection_id": c.id, "provider": c.provider,
        "repository_url": c.repository_url, "connection_status": c.status,
        "ref": s.ref, "commit_sha": s.commit_sha, "subdirectory": s.subdirectory, "tree_digest": s.tree_digest,
        "state": s.state, "runnable": s.runnable, "static_root": s.static_root,
        "file_count": s.file_count, "total_bytes": s.total_bytes, "skipped": s.skipped,
        "framework": s.framework, "analysis": s.analysis, "progress": s.progress,
        "error": {"code": s.error_code, "message": s.error_message} if s.error_code else None,
        "files_available": s.files_deleted_at is None and s.state == s.State.READY,
        "created_at": s.created_at.isoformat(), "updated_at": s.updated_at.isoformat(),
    }


def snapshot_summary(s):
    """목록용 (분석 본문 제외)."""
    item = snapshot_dict(s)
    item["analysis"] = {"status": (s.analysis or {}).get("status"), "counts": (s.analysis or {}).get("counts")}
    item["progress"] = s.progress[-1:]
    return item
