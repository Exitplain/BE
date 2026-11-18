"""Routes and navigation of a build-free static site (plain HTML + JS). Stdlib only, read-only.

Two shapes are read:
- multi-page: every HTML file is a page; its path is the URL (dir/index.html -> /dir)
- single page with a JS route table: an object literal whose keys are path strings, e.g. {'/a': viewA, '/b': viewB}.
  The value identifier is the view function; links found inside that function belong to that route.
Like extract_routes, this module holds no stage vocabulary (route patterns are the keys) and reports what it cannot
resolve in `unresolved` instead of guessing.
"""
import re
from pathlib import Path

from .detect_framework import SKIP_DIRS
from .extract_routes import classify, is_dynamic, line_of, navigations, norm, resolve_target

FN_DECL = re.compile(r"""^[ \t]*(?:async[ \t]+)?function[ \t]*\*?[ \t]*([A-Za-z_$][\w$]*)[ \t]*\(""", re.M)

HTML_EXTS = {".html", ".htm"}
SCRIPT_EXTS = {".js", ".mjs"}
# '/path': identifier   (keys of a route table)
TABLE_KEY = re.compile(r"""(?<![\w$])(['"])(/[^'"\s]*)\1\s*:\s*([A-Za-z_$][\w$]*)""")
# path === '/path'   (any identifier ending in path/pathname/route compared to a path string)
PATHNAME_CMP = re.compile(r"""\b\w*(?:[pP]ath(?:name)?|[rR]oute)\s*={2,3}\s*(['"])(/[^'"\s]*)\1""")
# path.match(/^\/item\/(\w+)$/)   (anchored regex literal over a path variable)
PATH_REGEX = re.compile(r"""\b\w*(?:[pP]ath(?:name)?|[rR]oute)\.match\(\s*/\^((?:\\.|[^/\\])*)\$/""")


def regex_pattern(body):
    """'\\/item\\/(\\w+)\\/info' -> '/item/:p1/info'. None when the regex is more than literal segments and groups."""
    out, n, i = "", 0, 0
    while i < len(body):
        if body.startswith("\\/", i):
            out, i = out + "/", i + 2
        elif body[i] == "(":
            close = body.find(")", i)
            if close < 0:
                return None
            n, out, i = n + 1, out + f":p{n + 1}", close + 1
        elif body[i].isalnum() or body[i] in "-_.":
            out, i = out + body[i], i + 1
        elif body.startswith("\\.", i):
            out, i = out + ".", i + 2
        else:
            return None
    return norm(out) if out.startswith("/") else None
# data-*="/path", location = '/path', location.href = '/path', location.assign('/path'), go('/path') style helpers
EXTRA_NAV = [
    ("link", re.compile(r"""\bdata-[\w-]+\s*=\s*(['"])(/[^'"\s$]*)\1""")),
    ("navigate", re.compile(r"""\blocation(?:\.href)?\s*=\s*(['"])(/[^'"\s]*)\1""")),
    ("navigate", re.compile(r"""\b(?:assign|go|goto|goTo|navigateTo|routeTo)\(\s*(['"])(/[^'"\s]*)\1""")),
]
STRING = re.compile(r"""([`'"])(/[^`'"\s]*)\1""")


def _files(root, exts):
    root = Path(root)
    for p in sorted(root.rglob("*")):
        if any(part in SKIP_DIRS for part in p.relative_to(root).parts):
            continue
        if p.is_file() and p.suffix.lower() in exts:
            yield p


def _enclosing_brace(text, pos):
    """Index of the '{' that encloses pos (string contents are not skipped; good enough for route tables)."""
    depth = 0
    for i in range(pos - 1, -1, -1):
        c = text[i]
        if c == "}":
            depth += 1
        elif c == "{":
            if depth == 0:
                return i
            depth -= 1
    return -1


def _close_brace(text, start):
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i + 1
    return len(text)


def fn_spans(text):
    """Function declarations at any indentation as (name, start, end); end found by brace matching."""
    spans = []
    for m in FN_DECL.finditer(text):
        brace = text.find("{", m.end())
        if brace >= 0:
            spans.append((m.group(1), m.start(), _close_brace(text, brace)))
    return spans


def html_routes(root):
    """Each HTML file is a page. Returns [{pattern, file, line, view}]."""
    routes = []
    for f in _files(root, HTML_EXTS):
        rel = f.relative_to(root).as_posix()
        if f.name.lower() in ("index.html", "index.htm"):
            pattern = norm("/" + rel.rsplit("/", 1)[0]) if "/" in rel else "/"
        else:
            pattern = "/" + rel
        routes.append({"pattern": pattern, "file": rel, "line": 1, "view": None, "html": f})
    return routes


def table_routes(root):
    """Route tables in scripts: object literals with >= 2 path-string keys. Returns (routes, table_spans)."""
    routes, spans = [], {}
    for f in _files(root, SCRIPT_EXTS):
        text = f.read_text("utf-8", errors="replace")
        groups = {}
        for m in TABLE_KEY.finditer(text):
            groups.setdefault(_enclosing_brace(text, m.start()), []).append(m)
        rel = f.relative_to(root).as_posix()
        for brace, found in groups.items():
            if brace < 0 or len(found) < 2:
                continue
            spans.setdefault(str(f.resolve()), []).append((brace, _close_brace(text, brace)))
            for m in found:
                pattern = norm(m.group(2))
                if pattern:
                    routes.append({"pattern": pattern, "file": rel, "line": line_of(text, m.start()), "view": m.group(3)})
        for m in PATHNAME_CMP.finditer(text):
            pattern = norm(m.group(2))
            if pattern:
                routes.append({"pattern": pattern, "file": rel, "line": line_of(text, m.start()), "view": _view_after(text, m.end())})
        for m in PATH_REGEX.finditer(text):
            pattern = regex_pattern(m.group(1))
            if pattern:
                routes.append({"pattern": pattern, "file": rel, "line": line_of(text, m.start()), "view": _view_after(text, m.end())})
    return routes, spans


def _view_after(text, pos):
    """`if (path === '/x') return view();` → 'view' (the function called right after the test, same line)."""
    end = text.find("\n", pos)
    m = re.match(r"""[^\n;{]*?\)\s*\)?\s*return\s+([A-Za-z_$][\w$]*)\s*\(""", text[pos:end if end >= 0 else len(text)])
    return m.group(1) if m else None


def _nav_hits(f, text):
    """(kind, raw, pos) for every navigation-looking expression in a file."""
    for kind, (typ, _), _, raw, pos in navigations(f):
        if typ != "skip":
            yield kind, raw, pos
    for kind, rx in EXTRA_NAV:
        for m in rx.finditer(text):
            yield kind, m.group(2), m.start()


def extract_static(root):
    """Same keys as extract_routes.extract (routes, edges, global_edges, unresolved) plus `spa`."""
    root = Path(root).resolve()
    unresolved = []
    table, spans = table_routes(root)
    pages = html_routes(root)
    spa = bool(table)
    # a single-page app serves every table route from its one HTML file; other HTML files are separate pages
    routes = {}
    for r in table + ([p for p in pages if p["pattern"] != "/"] if spa else pages):
        if r["pattern"] in routes:
            if r.get("view") and not routes[r["pattern"]].get("view"):
                routes[r["pattern"]]["view"] = r["view"]
            continue
        routes[r["pattern"]] = r
    if spa and "/" not in routes:
        root_page = next((p for p in pages if p["pattern"] == "/"), None)
        if root_page:
            routes["/"] = root_page
    patterns = set(routes)
    views = {}
    for pattern, r in routes.items():
        if r.get("view"):
            views.setdefault(r["view"], []).append(pattern)

    edges, global_edges, seen = [], [], set()
    for f in list(_files(root, SCRIPT_EXTS)) + list(_files(root, HTML_EXTS)):
        text = f.read_text("utf-8", errors="replace")
        rel = f.relative_to(root).as_posix()
        fkey = str(f.resolve())
        skip_spans = spans.get(fkey, [])
        page_owner = [r["pattern"] for r in routes.values() if r.get("html") and Path(r["html"]).resolve() == f.resolve()]
        scopes = fn_spans(text) if f.suffix.lower() in SCRIPT_EXTS else []

        def owners_at(pos):
            if page_owner and not spa:
                return page_owner
            # innermost view function around pos
            inside = sorted((b - a, name) for name, a, b in scopes if a <= pos < b and name in views)
            return views[inside[0][1]] if inside else []

        def add(kind, raw, pos):
            if any(a <= pos < b for a, b in skip_spans):
                return
            typ, val = classify(raw)
            if typ not in ("abs", "tpl") or not val:
                return
            dest = val if val in patterns else resolve_target(val, patterns)
            if dest is None:
                # only real navigation expressions count as unresolved; a file path (has an extension) never does
                if typ == "tpl" and kind != "mention" and not re.search(r"\.\w+$", val):
                    unresolved.append({"kind": "dynamic_navigation", "file": rel, "line": line_of(text, pos), "detail": raw})
                return
            val = dest
            line = line_of(text, pos)
            for src in owners_at(pos) or [None]:
                key = (src, val, rel, line)
                if key in seen or src == val:
                    continue
                seen.add(key)
                if src is None:
                    global_edges.append({"to": val, "kind": kind, "file": rel, "line": line})
                else:
                    edges.append({"from": src, "to": val, "kind": kind, "file": rel, "line": line})

        for kind, raw, pos in _nav_hits(f, text):
            add(kind, raw, pos)
        # a known route path passed around as a string inside a view (e.g. a link helper's argument)
        for m in STRING.finditer(text):
            add("mention", m.group(2), m.start())

    out_routes = [{"pattern": p, "file": r["file"], "line": r["line"], "files": [r["file"]],
                   "dynamic": is_dynamic(p)} for p, r in sorted(routes.items())]
    return {"routes": out_routes, "edges": edges, "global_edges": global_edges, "unresolved": unresolved, "spa": spa}
