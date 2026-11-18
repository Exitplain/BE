"""받은 코드의 규칙 분석: 프레임워크 감지 → 라우트·화면 이동 추출 → 실행 가능 판정. 코드를 실행하지 않는다.

분석기 본체는 agent/source (프레임워크별 분기 + 정적 사이트 분기). 여기서는 결과를 저장용으로 줄이고,
'URL 없이 바로 열 수 있는지(정적)'·'빌드가 필요한지'만 판정한다. 빌드는 하지 않는다 (격리된 실행 환경 전, SRC-07).
"""
import json
from pathlib import Path

from agent.source.extract_routes import extract

ROUTE_LIMIT = 200
EDGE_LIMIT = 500
BUILT_DIRS = ("dist", "build", "out")   # 빌드 결과물로 흔히 쓰는 폴더 (public 은 원본 템플릿이라 넣지 않는다)


def runnable(root):
    """(static|needs_build|unknown, 정적으로 열 폴더). 빌드 결과물이 같이 있으면 그 폴더를 정적으로 연다."""
    root = Path(root)
    pkg = root / "package.json"
    if not pkg.is_file():
        return ("static", "") if (root / "index.html").is_file() else ("unknown", "")
    for name in BUILT_DIRS:
        if (root / name / "index.html").is_file():
            return "static", name
    try:
        scripts = json.loads(pkg.read_text("utf-8")).get("scripts") or {}
    except (ValueError, OSError):
        scripts = {}
    return ("needs_build", "") if "build" in scripts else ("unknown", "")


def analyze(root):
    """저장할 분석 요약. 경로는 모두 root 기준 상대 경로."""
    out = extract(Path(root))
    framework = out["framework"]
    kind, static_root = runnable(root)
    return {
        "framework": {k: framework.get(k) for k in ("framework", "router", "version", "confidence", "status", "reason")},
        "runnable": kind,
        "static_root": static_root,
        "analysis": {
            "status": "ok" if out["routes"] else "blocked",
            "reason": None if out["routes"] else (out.get("reason") or framework.get("reason")),
            "spa": out.get("spa"),
            "routes": out["routes"][:ROUTE_LIMIT],
            "edges": out["edges"][:EDGE_LIMIT],
            "global_edges": out["global_edges"][:EDGE_LIMIT],
            "unresolved": out["unresolved"][:EDGE_LIMIT],
            "counts": {"routes": len(out["routes"]), "edges": len(out["edges"]),
                       "global_edges": len(out["global_edges"]), "unresolved": len(out["unresolved"])},
        },
    }
