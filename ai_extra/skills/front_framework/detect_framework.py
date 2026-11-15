"""Detect the front-end framework and router of a source tree (read-only, stdlib only).

Never guesses: ambiguous or unsupported trees come back as status="blocked" with a reason.
"""
import json
import re
from pathlib import Path

SKIP_DIRS = {"node_modules", ".git", "dist", "build", ".next", ".nuxt", "__pycache__"}
SOURCE_EXTS = {".js", ".jsx", ".ts", ".tsx", ".vue"}


def iter_files(root, exts=SOURCE_EXTS):
    root = Path(root)
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).parts
        if any(part in SKIP_DIRS for part in rel):
            continue
        if p.is_file() and p.suffix in exts:
            yield p


def read_deps(root):
    pkg = Path(root) / "package.json"
    if not pkg.is_file():
        return None, None
    text = pkg.read_text("utf-8")
    try:
        data = json.loads(text)
    except ValueError:
        return None, None
    deps = {}
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        deps.update(data.get(key) or {})
    return deps, text


def _major(version):
    m = re.search(r"(\d+)", version or "")
    return int(m.group(1)) if m else None


def _dep_line(text, name):
    for i, line in enumerate(text.splitlines(), 1):
        if f'"{name}"' in line:
            return i
    return None


def _first_dir(root, names):
    for name in names:
        d = Path(root) / name
        if d.is_dir():
            return d
    return None


def next_layout(root):
    """Return (app_dir, pages_dir) when they contain route files."""
    app = _first_dir(root, ["app", "src/app"])
    pages = _first_dir(root, ["pages", "src/pages"])
    has_app = bool(app and any(p.stem == "page" for p in iter_files(app)))
    has_pages = bool(pages and any(
        p.stem not in {"_app", "_document", "_error"} and "api" not in p.relative_to(pages).parts
        for p in iter_files(pages)))
    return (app if has_app else None), (pages if has_pages else None)


def detect(root):
    root = Path(root)
    result = {"framework": None, "router": None, "version": None, "confidence": "none",
              "status": "blocked", "reason": None, "evidence": []}
    deps, text = read_deps(root)
    if deps is None:
        result["reason"] = "no_package_json"
        return result

    def ev(name):
        result["evidence"].append({"file": "package.json", "line": _dep_line(text, name), "dependency": name})

    candidates = []
    if "next" in deps:
        app, pages = next_layout(root)
        if app and pages:
            candidates.append(("next", "next-mixed", deps["next"], "medium", "next"))
        elif app:
            candidates.append(("next", "next-app", deps["next"], "high", "next"))
        elif pages:
            candidates.append(("next", "next-pages", deps["next"], "high", "next"))
        else:
            result.update(framework="next", reason="no_routes_found", version=deps["next"])
            ev("next")
            return result
    rr = "react-router-dom" if "react-router-dom" in deps else ("react-router" if "react-router" in deps else None)
    if rr:
        major = _major(deps[rr])
        router = "react-router-v5" if major is not None and major < 6 else "react-router-v6"
        candidates.append(("react", router, deps[rr], "high", rr))
    if "vue-router" in deps:
        major = _major(deps["vue-router"])
        candidates.append(("vue", "vue-router-v3" if major == 3 else "vue-router-v4", deps["vue-router"], "high", "vue-router"))

    if len(candidates) > 1:
        result.update(reason="framework_ambiguous", confidence="low")
        for c in candidates:
            ev(c[4])
        result["candidates"] = [c[1] for c in candidates]
        return result
    if candidates:
        framework, router, version, confidence, dep = candidates[0]
        result.update(framework=framework, router=router, version=version, confidence=confidence, status="ok")
        ev(dep)
        return result
    if "react" in deps or "vue" in deps:
        name = "react" if "react" in deps else "vue"
        result.update(framework=name, router="none", version=deps[name], confidence="high",
                      status="blocked", reason="no_router_detected")
        ev(name)
        return result
    result["reason"] = "framework_unsupported"
    return result


if __name__ == "__main__":
    import sys
    print(json.dumps(detect(sys.argv[1]), ensure_ascii=False, indent=2))
