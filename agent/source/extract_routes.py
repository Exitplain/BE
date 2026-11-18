"""Static route/navigation extraction for local (source-available) targets. Stdlib only, read-only.

Derives routes, navigation edges and a funnel path from the source tree. It contains no
vocabulary of stage names: stage keys are the discovered route patterns. Anything that cannot
be resolved statically is reported in `unresolved` instead of being guessed.
"""
import json
import re
from collections import deque
from pathlib import Path

from .detect_framework import SOURCE_EXTS, detect, iter_files, next_layout

ROUTE_EXTS = (".tsx", ".ts", ".jsx", ".js", ".vue")
ATTR_PATH = r'''(?:\bto|\bhref)\s*=\s*(?:"([^"]*)"|'([^']*)'|\{\s*(?:"([^"]*)"|'([^']*)'|`([^`]*)`)\s*\})'''
CALL_PATH = r'''\b(navigate|push|replace|redirect|permanentRedirect)\(\s*(?:"([^"]*)"|'([^']*)'|`([^`]*)`)'''
OBJ_PATH = r'''\b(?:push|replace)\(\s*\{\s*path:\s*(?:"([^"]*)"|'([^']*)')'''


def line_of(text, pos):
    return text.count("\n", 0, pos) + 1


def norm(path):
    path = path.split("#")[0].split("?")[0].strip()
    if not path.startswith("/"):
        return None
    if len(path) > 1:
        path = path.rstrip("/")
    return path or "/"


def join(parent, child):
    if child.startswith("/"):
        return child
    if child in ("", "."):
        return parent
    return (parent.rstrip("/") + "/" + child) if parent != "/" else "/" + child


def is_dynamic(pattern):
    return bool(re.search(r":\w|\[|\*", pattern))


def pattern_regex(pattern):
    parts = []
    for seg in pattern.strip("/").split("/"):
        if not seg:
            continue
        out = ""
        for tok in re.split(r"(:\w+\??|\[\.\.\.\w+\]|\[\w+\]|\*)", seg):
            if re.fullmatch(r"\[\.\.\.\w+\]|\*", tok):
                out += ".+"
            elif re.fullmatch(r":\w+\??|\[\w+\]", tok):
                out += "[^/]+"
            else:
                out += re.escape(tok)
        parts.append(out)
    return re.compile("^/" + "/".join(parts) + "$")


def resolve_target(target, patterns):
    if target in patterns:
        return target
    matches = [pat for pat in patterns if is_dynamic(pat) and pattern_regex(pat).match(target)]
    if not matches:
        return None

    def specificity(pat):  # routers prefer static segments over params, and params over splats
        segs = [x for x in pat.strip("/").split("/") if x]
        splats = sum(1 for x in segs if x == "*" or x.startswith("[..."))
        params = sum(1 for x in segs if re.search(r":\w|\[", x)) - splats
        return (splats, params, -len(segs), pat)
    return min(matches, key=specificity)


def tag_name_before(text, pos):
    start = text.rfind("<", 0, pos)
    m = re.match(r"<([\w.]+)", text[start:pos]) if start != -1 else None
    return m.group(1) if m else ""


def classify(raw):
    """('abs'|'rel'|'tpl'|'skip', value). Template literals keep a placeholder for ${...}."""
    raw = raw.strip()
    if not raw or raw.startswith("#") or raw.startswith("//") or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", raw):
        return "skip", None
    if "${" in raw:
        tpl = re.sub(r"\$\{[^}]*\}", "__v__", raw)
        return ("tpl", norm(tpl)) if tpl.startswith("/") else ("tpl_rel", tpl)
    if raw.startswith("/"):
        return "abs", norm(raw)
    return "rel", raw


def navigations(path):
    """Yield (kind, (type, value), line, raw, pos) for every navigation expression in a file."""
    text = Path(path).read_text("utf-8", errors="replace")
    for m in re.finditer(ATTR_PATH, text):
        raw = next(g for g in m.groups() if g is not None)
        kind = "redirect" if tag_name_before(text, m.start()) == "Navigate" else "link"
        yield kind, classify(raw), line_of(text, m.start()), raw, m.start()
    for m in re.finditer(CALL_PATH, text):
        raw = next(g for g in m.groups()[1:] if g is not None)
        kind = "redirect" if m.group(1) in {"redirect", "permanentRedirect"} else "navigate"
        yield kind, classify(raw), line_of(text, m.start()), raw, m.start()
    for m in re.finditer(OBJ_PATH, text):
        raw = next(g for g in m.groups() if g is not None)
        yield "navigate", classify(raw), line_of(text, m.start()), raw, m.start()


def resolve_import(from_file, spec, root):
    if not spec.startswith("."):
        return None
    base = (Path(from_file).parent / spec).resolve()
    for cand in [base] + [base.with_suffix(e) for e in ROUTE_EXTS] + [base / f"index{e}" for e in ROUTE_EXTS]:
        if cand.is_file():
            return cand
    return None


def import_map(file, root):
    """component name -> resolved file, from default imports and lazy imports."""
    text = Path(file).read_text("utf-8", errors="replace")
    out = {}
    for m in re.finditer(r"import\s+(\w+)\s+from\s+[\"']([^\"']+)[\"']", text):
        target = resolve_import(file, m.group(2), root)
        if target:
            out[m.group(1)] = target
    for m in re.finditer(r"import\s*\{([^}]*)\}\s*from\s*[\"']([^\"']+)[\"']", text):
        target = resolve_import(file, m.group(2), root)
        if target:
            for part in m.group(1).split(","):
                alias = part.split(" as ")[-1].strip()
                if alias:
                    out[alias] = target
    for m in re.finditer(r"(\w+)\s*=\s*(?:React\.)?lazy\(\s*\(\)\s*=>\s*import\([\"']([^\"']+)[\"']\)", text):
        target = resolve_import(file, m.group(2), root)
        if target:
            out[m.group(1)] = target
    return out


def definition_map(root):
    out = {}
    for f in iter_files(root, {".tsx", ".jsx", ".ts", ".js"}):
        text = f.read_text("utf-8", errors="replace")
        for m in re.finditer(r"(?:function|const|class)\s+([A-Z]\w*)", text):
            out.setdefault(m.group(1), f.resolve())
    return out


def jsx_open_tag(text, start):
    """Return (tag_text, end_index) scanning to the closing '>' outside of braces."""
    depth = 0
    i = start
    quote = None
    while i < len(text):
        c = text[i]
        if quote:
            if c == quote:
                quote = None
        elif c in "\"'":
            quote = c
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        elif c == ">" and depth == 0:
            return text[start:i + 1], i + 1
        i += 1
    return text[start:], len(text)


def component_of(attrs):
    m = (re.search(r"element\s*=\s*\{\s*<\s*(\w+)", attrs) or re.search(r"element\s*:\s*<\s*(\w+)", attrs)
         or re.search(r"[Cc]omponent\s*=\s*\{\s*(\w+)", attrs))
    return m.group(1) if m else None


COL0 = re.compile(r"^(?=[A-Za-z])", re.M)
DECL = re.compile(r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function\s*\*?\s*|const\s+|let\s+|class\s+)([A-Z]\w*)", re.M)
_scope_cache = {}


def top_scopes(path):
    """Capitalised top-level declarations as (name, start, end). Assumes column-0 statements (prettier style)."""
    key = str(path)
    if key not in _scope_cache:
        text = Path(path).read_text("utf-8", errors="replace")
        starts = [m.start() for m in COL0.finditer(text)]
        out = []
        for m in DECL.finditer(text):
            later = [s for s in starts if s > m.start()]
            out.append((m.group(1), m.start(), later[0] if later else len(text)))
        _scope_cache[key] = out
    return _scope_cache[key]


FN_DECL = re.compile(r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*([a-z_]\w*)", re.M)
_fn_cache = {}


def fn_scopes(path):
    key = str(path)
    if key not in _fn_cache:
        text = Path(path).read_text("utf-8", errors="replace")
        starts = [m.start() for m in COL0.finditer(text)]
        out = []
        for m in FN_DECL.finditer(text):
            later = [s for s in starts if s > m.start()]
            out.append((m.group(1), m.start(), later[0] if later else len(text)))
        _fn_cache[key] = out
    return _fn_cache[key]


def default_export(path):
    text = Path(path).read_text("utf-8", errors="replace")
    m = (re.search(r"export\s+default\s+(?:async\s+)?(?:function\s*\*?\s*|class\s+)([A-Za-z_]\w*)", text)
         or re.search(r"export\s+default\s+([A-Za-z_]\w*)\s*;?\s*$", text, re.M))
    return m.group(1) if m else None


def scope_name_at(path, pos):
    for name, a, b in top_scopes(path) + fn_scopes(path):
        if a <= pos < b:
            return name
    return None


def match_close(text, i):
    depth = 0
    for j in range(i, len(text)):
        c = text[j]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                return j
    return len(text) - 1


def split_top(text, start, end):
    pieces, depth, s = [], 0, start
    for j in range(start, end):
        c = text[j]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "," and depth == 0:
            pieces.append((s, j))
            s = j + 1
    if text[s:end].strip():
        pieces.append((s, end))
    return pieces


KEY = re.compile(r"\s*(?:async\s+)?([A-Za-z_]\w*)\s*(?=[:(])")
TAGS = re.compile(r"<\s*([A-Z]\w*)(?![\w.])")
ROUTEISH = {"element", "Component", "component", "components", "children", "lazy", "redirect", "loader"}


def prop_of(text, s, e):
    m = KEY.match(text, s, e)
    if not m:
        return None
    nxt = m.end()
    return m.group(1), (nxt + 1 if text[nxt] == ":" else nxt), text[nxt] != ":"


def str_lit(val):
    m = re.match(r"\s*(?:\"([^\"]*)\"|'([^']*)'|`([^`$]*)`)", val)
    return next(g for g in m.groups() if g is not None) if m else None


def object_keys(text, ob, oe):
    return [p[0] for p in (prop_of(text, s, e) for s, e in split_top(text, ob + 1, oe)) if p]


def parse_route_array(text, lb, ctx, parent, out):
    """Parse an array of route objects (nested via children) into flat dicts with full patterns."""
    rb = match_close(text, lb)
    for ps, pe in split_top(text, lb + 1, rb):
        seg = text[ps:pe]
        ob = ps + (len(seg) - len(seg.lstrip()))
        if not text.startswith("{", ob):
            continue
        oe = match_close(text, ob)
        node = {"path": None, "index": False, "children": None, "component": None, "component_file": None,
                "components": [], "inline": None, "redirect": None, "fns": [], "fn_inlines": [], "lazy": None}
        for qs, qe in split_top(text, ob + 1, oe):
            prop = prop_of(text, qs, qe)
            if not prop:
                continue
            key, vs, method = prop
            val = text[vs:qe]
            if key == "path":
                lit = str_lit(val)
                if lit is None:
                    ctx["unresolved"].append({"kind": "unresolved_path", "file": ctx["rel"],
                                              "line": line_of(text, qs), "detail": val.strip()[:60]})
                    node["bad"] = True
                node["path"], node["path_line"] = lit, line_of(text, qs)
            elif key == "index":
                node["index"] = "true" in val
            elif key == "children":
                v = val.lstrip()
                if v.startswith("["):
                    node["children"] = vs + (len(val) - len(v))
            elif key == "redirect":
                node["redirect"] = str_lit(val)
            elif key == "element":
                names = TAGS.findall(val)
                if names:
                    node["components"] = names
                    node["component"] = names[0]
                else:
                    node["inline"] = (qs, qe)
            elif key in ("loader", "action"):
                nm = re.match(r"\s*([A-Za-z_]\w*)\s*$", val)
                if method or "=>" in val or val.lstrip().startswith("function") or val.lstrip().startswith("async"):
                    node["fn_inlines"].append((qs, qe))
                elif nm:
                    node["fns"].append(nm.group(1))
            elif key == "lazy":
                imp = re.search(r"import\(\s*[\"']([^\"']+)[\"']\s*\)", val)
                if imp:
                    node["lazy"] = (resolve_import(ctx["file"], imp.group(1), ctx["root"]),
                                    re.findall(r"Component\s*:\s*([A-Z]\w*)", val))
            elif key in ("Component", "component", "components"):
                imp = re.search(r"import\(\s*[\"']([^\"']+)[\"']\s*\)", val)
                nm = re.match(r"\s*(?:\{\s*default\s*:\s*)?([A-Za-z_]\w*)\s*(?:[,}]|$)", val)
                if imp:
                    node["component_file"] = resolve_import(ctx["file"], imp.group(1), ctx["root"])
                elif method or "=>" in val or "function" in val[:12]:
                    node["inline"] = (qs, qe)
                elif nm:
                    node["component"] = nm.group(1)
                    node["components"] = [nm.group(1)]
        if node.get("bad"):
            continue
        if node["path"] is None:
            full = parent
        else:
            full = join(parent, node["path"])
        full = norm(full) or full
        comps = [(n, ctx["imports"].get(n) or ctx["defs"].get(n)) for n in node["components"]]
        if node["component_file"]:
            comps.append((None, node["component_file"]))
        if node["lazy"] and node["lazy"][0]:
            lazy_file, lazy_names = node["lazy"]
            comps += [(n, lazy_file) for n in lazy_names] or [("?", lazy_file)]
        fn_names = [(n, ctx["imports"].get(n) or (ctx["file"] if any(a == n for a, _, _ in fn_scopes(ctx["file"])) else None))
                    for n in node["fns"]]
        inl = ([(ctx["file"],) + node["inline"]] if node["inline"] else []) + [(ctx["file"],) + sp for sp in node["fn_inlines"]]
        out.append({"pattern": full, "local": full, "file": ctx["rel"], "line": node.get("path_line") or line_of(text, ob),
                    "components": comps + fn_names, "inlines": inl, "pathless": node["path"] is None,
                    "src": ctx["file"], "pos": ob, "redirect": node["redirect"],
                    "layout": node["children"] is not None})
        if node["children"] is not None:
            parse_route_array(text, node["children"], ctx, full, out)


def object_routes(f, root, defs, unresolved):
    text = f.read_text("utf-8", errors="replace")
    ctx = {"file": f, "rel": str(f.relative_to(root)), "root": root, "imports": import_map(f, root),
           "defs": defs, "unresolved": unresolved}
    out, covered = [], []
    for m in re.finditer(r"\[\s*\{", text):
        lb = m.start()
        if any(a <= lb <= b for a, b in covered):
            continue
        rb = match_close(text, lb)
        pieces = [(s, e) for s, e in split_top(text, lb + 1, rb) if text[s:e].lstrip().startswith("{")]
        keysets = []
        for s, e in pieces:
            ob = s + (len(text[s:e]) - len(text[s:e].lstrip()))
            keysets.append(set(object_keys(text, ob, match_close(text, ob))))
        if not any(("path" in k or "index" in k) and (k & ROUTEISH) for k in keysets):
            continue
        covered.append((lb, rb))
        parse_route_array(text, lb, ctx, "/", out)
    return out


def jsx_routes(f, root, defs):
    text = f.read_text("utf-8", errors="replace")
    imports = import_map(f, root)
    out, stack = [], []
    for m in re.finditer(r"<Route\b|</Route\s*>", text):
        if m.group(0).startswith("</"):
            if stack:
                stack.pop()
            continue
        tag, end = jsx_open_tag(text, m.start())
        selfclose = tag.rstrip(">").rstrip().endswith("/")
        pm = re.search(r"\bpath\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|\{\s*[\"']([^\"']*)[\"']\s*\})", tag)
        parent = stack[-1] if stack else "/"
        full = join(parent, next(g for g in pm.groups() if g is not None)) if pm else parent
        full = norm(full) or full
        names = [n for n in TAGS.findall(tag) if n != "Route"]
        cm = re.search(r"[Cc]omponent\s*=\s*\{\s*([A-Z]\w*)", tag)
        if cm:
            names.append(cm.group(1))
        fn_names = [(n, imports.get(n) or (f if any(a == n for a, _, _ in fn_scopes(f)) else None))
                    for n in re.findall(r"\b(?:loader|action)\s*=\s*\{\s*([A-Za-z_]\w*)\s*\}", tag)]
        out.append({"pattern": full, "local": full, "file": str(f.relative_to(root)), "line": line_of(text, m.start()),
                    "pathless": pm is None,
                    "components": [(n, imports.get(n) or defs.get(n)) for n in names] + fn_names,
                    "inlines": [(f, m.start(), end)] if not names else [], "src": f, "pos": m.start(),
                    "redirect": None, "layout": not selfclose})
        if not selfclose:
            stack.append(full)
    return out


def js_routes(root, unresolved):
    defs = definition_map(root)
    routes = []
    for f in iter_files(root, {".tsx", ".jsx", ".ts", ".js"}):
        routes += jsx_routes(f, root, defs)
        routes += object_routes(f, root, defs, unresolved)
    return routes


def next_app_routes(root, app_dir, unresolved):
    routes = []
    for f in iter_files(app_dir, {".tsx", ".ts", ".jsx", ".js"}):
        if f.stem != "page":
            continue
        segs = []
        flagged = False
        for seg in f.relative_to(app_dir).parent.parts:
            if re.fullmatch(r"\(\.+\).*", seg):
                flagged = True
                unresolved.append({"kind": "intercepting_route", "file": str(f.relative_to(root)), "line": 1, "detail": seg})
            elif seg.startswith("@"):
                flagged = True
                unresolved.append({"kind": "parallel_route", "file": str(f.relative_to(root)), "line": 1, "detail": seg})
            elif re.fullmatch(r"\(.*\)", seg):
                continue
            else:
                segs.append(seg)
        pattern = "/" + "/".join(segs)
        routes.append({"pattern": pattern, "local": pattern, "file": str(f.relative_to(root)), "line": 1,
                       "components": [(None, f.resolve())], "inlines": [], "src": f, "pos": 0, "redirect": None,
                       "layout": False, "flagged": flagged})
    return routes


def next_pages_routes(root, pages_dir):
    routes = []
    for f in iter_files(pages_dir, {".tsx", ".ts", ".jsx", ".js"}):
        rel = f.relative_to(pages_dir)
        if f.stem in {"_app", "_document", "_error"} or "api" in rel.parts:
            continue
        parts = list(rel.parent.parts)
        if f.stem != "index":
            parts.append(f.stem)
        pattern = "/" + "/".join(parts)
        routes.append({"pattern": pattern, "local": pattern, "file": str(f.relative_to(root)), "line": 1,
                       "components": [(None, f.resolve())], "inlines": [], "src": f, "pos": 0, "redirect": None,
                       "layout": False})
    return routes


def reroot_mounted(routes):
    """Route trees declared inside a component that another route mounts (e.g. under 'dashboard/*') are relative to it."""
    groups = {}
    for r in routes:
        groups.setdefault((str(Path(r["src"]).resolve()), scope_name_at(r["src"], r["pos"])), []).append(r)
    base_cache = {}

    def mount_of(gkey):
        f, container = gkey
        if container is None:
            return None
        for g2, members in groups.items():
            if g2 == gkey:
                continue
            for r2 in members:
                for name, cf in r2["components"]:
                    if cf and str(Path(cf).resolve()) == f and (name == container or default_export(f) == container):
                        return r2
        return None

    def base_of(gkey, seen=()):
        if gkey in base_cache:
            return base_cache[gkey]
        base = ""
        r2 = mount_of(gkey) if gkey not in seen else None
        if r2 is not None:
            g2 = (str(Path(r2["src"]).resolve()), scope_name_at(r2["src"], r2["pos"]))
            parent_base = base_of(g2, seen + (gkey,))
            pat = join(parent_base or "/", r2["local"].lstrip("/")) if parent_base else r2["local"]
            base = pat[:-2] if pat.endswith("/*") else pat
            base = "" if base == "/" else base
        base_cache[gkey] = base
        return base

    for gkey, members in groups.items():
        base = base_of(gkey)
        if base:
            for r in members:
                r["pattern"] = (base if r["local"] == "/" else base + r["local"]) if r["local"].startswith("/") else base + "/" + r["local"]
    return routes


def humanize(pattern):
    if pattern == "/":
        return "(root)"
    seg = pattern.strip("/").split("/")[-1]
    return re.sub(r"[-_]+", " ", re.sub(r"[:\[\]\.]", "", seg)).strip() or pattern


def extract(root, entry="/", success=None):
    root = Path(root).resolve()
    detection = detect(root)
    out = {"schema_version": "1.0", "mode": "local", "verification": "unverified", "framework": detection,
           "status": "blocked", "reason": detection.get("reason"), "routes": [], "edges": [], "global_edges": [],
           "unresolved": [], "funnel": None}
    if detection["status"] != "ok":
        return out
    unresolved = out["unresolved"]
    router = detection["router"]
    if router == "static-html":
        from .static_site import extract_static
        found = extract_static(root)
        out.update({k: found[k] for k in ("routes", "edges", "global_edges")}, spa=found["spa"])
        unresolved += found["unresolved"]
        if not out["routes"]:
            out["reason"] = "no_routes_found"
            return out
        dynamic = {r["pattern"]: r["dynamic"] for r in out["routes"]}
        return _funnel(out, set(dynamic), dynamic, out["edges"], entry, success)
    if router.startswith("react-router") or router.startswith("vue-router"):
        routes = js_routes(root, unresolved)
        routes = reroot_mounted(routes)
    else:
        app, pages = next_layout(root)
        routes = (next_app_routes(root, app, unresolved) if app else []) + (next_pages_routes(root, pages) if pages else [])
    # a parent route is a pure layout (its own links are global) when it is pathless, is the root, or an index
    # child shares its URL; a parent with its own distinct path (e.g. /todos with a :id child) is still a page
    counts = {}
    for r in routes:
        counts[r["pattern"]] = counts.get(r["pattern"], 0) + 1
    routes = [r for r in routes if not (r.get("layout") and (r.get("pathless") or r["pattern"] == "/" or counts[r["pattern"]] > 1))]
    merged = {}
    for r in routes:
        if not r["pattern"]:
            continue
        if r["pattern"] in merged:  # the same path declared twice (e.g. a modal overlay tree)
            merged[r["pattern"]]["components"] += r["components"]
            merged[r["pattern"]]["inlines"] += r["inlines"]
            if r.get("redirect") and not merged[r["pattern"]].get("redirect"):
                merged[r["pattern"]]["redirect"] = r["redirect"]
        else:
            merged[r["pattern"]] = dict(r)
    routes = list(merged.values())
    if not routes:
        out["reason"] = "no_routes_found"
        return out
    patterns = {r["pattern"] for r in routes}
    inline_spans, scope_owner = {}, {}
    for r in routes:
        for f, a_, b_ in r["inlines"]:
            inline_spans.setdefault(str(Path(f).resolve()), []).append((a_, b_, r["pattern"]))
        for name, cf in r["components"]:
            if not cf:
                continue
            cf = str(Path(cf).resolve())
            names = {n for n, _, _ in top_scopes(cf)} if not cf.endswith(".vue") else set()
            fnames = {n for n, _, _ in fn_scopes(cf)}
            if name is None:  # file-based route (Next/Vue): the whole page file belongs to the route
                key = (cf, None)
            elif name == "?":  # lazy import without a named component: only claim the file when it is unambiguous
                dflt = default_export(cf)
                if dflt in names:
                    key = (cf, dflt)
                elif len(names) == 1:
                    key = (cf, next(iter(names)))
                else:
                    continue
            elif name in names or name in fnames:
                key = (cf, name)
            elif names:
                dflt = default_export(cf)
                key = (cf, dflt) if dflt in names else ((cf, next(iter(names))) if len(names) == 1 else (cf, None))
            else:
                key = (cf, None)
            if r["pattern"] not in scope_owner.setdefault(key, []):
                scope_owner[key].append(r["pattern"])
    edges = []
    for f in iter_files(root, SOURCE_EXTS):
        fkey = str(f.resolve())
        rel = str(f.relative_to(root))
        for kind, (typ, val), line, raw, pos in navigations(f):
            if typ == "skip":
                continue
            owners = [pat for a_, b_, pat in inline_spans.get(fkey, []) if a_ <= pos < b_]
            if not owners:
                name = scope_name_at(fkey, pos) if not fkey.endswith(".vue") else None
                owners = scope_owner.get((fkey, name)) or scope_owner.get((fkey, None)) or []
            if typ in ("abs", "tpl") and val and val.startswith("/"):
                dest = resolve_target(val, patterns)
                if dest is None and typ == "tpl":
                    unresolved.append({"kind": "dynamic_navigation", "file": rel, "line": line, "detail": raw})
                    continue
                dests = [dest] if dest else []
            elif typ in ("rel", "tpl_rel") and owners:
                dests = [d for d in (resolve_target(norm(join(o, val)) or "", patterns) for o in owners) if d]
                dests = dests[:1] if len(owners) == 1 else dests
                if not dests:
                    if typ == "tpl_rel":
                        unresolved.append({"kind": "dynamic_navigation", "file": rel, "line": line, "detail": raw})
                    continue
            else:
                if typ in ("tpl", "tpl_rel", "rel"):
                    unresolved.append({"kind": "dynamic_navigation", "file": rel, "line": line, "detail": raw})
                continue
            for dest in dests:
                if owners:
                    for src in owners:
                        edges.append({"from": src, "to": dest, "kind": kind, "file": rel, "line": line})
                else:
                    out["global_edges"].append({"to": dest, "kind": kind, "file": rel, "line": line})
    for r in routes:
        if r.get("redirect"):
            tgt = norm(r["redirect"]) if isinstance(r["redirect"], str) else None
            dest = resolve_target(tgt, patterns) if tgt else None
            if dest:
                edges.append({"from": r["pattern"], "to": dest, "kind": "redirect", "file": r["file"], "line": r["line"]})
    def rel_files(r):
        found = set()
        for _, cf in r["components"]:
            if cf:
                try:
                    found.add(str(Path(cf).resolve().relative_to(root)))
                except ValueError:
                    pass
        return sorted(found)
    out["routes"] = [{"pattern": r["pattern"], "file": r["file"], "line": r["line"], "files": rel_files(r),
                      "dynamic": is_dynamic(r["pattern"]) or bool(r.get("flagged"))} for r in routes]
    out["edges"] = edges
    dynamic = {r["pattern"]: (is_dynamic(r["pattern"]) or bool(r.get("flagged"))) for r in routes}
    return _funnel(out, patterns, dynamic, edges, entry, success)


def _funnel(out, patterns, dynamic, edges, entry, success):
    """Shortest navigation path entry -> success over the extracted edges (shared by every router branch)."""
    if success is None:
        out["reason"] = "success_rule_required"
        return out
    entry_pat = resolve_target(entry, patterns)
    success_pat = resolve_target(success, patterns)
    if entry_pat is None:
        out["reason"] = "entry_not_found"
        return out
    if success_pat is None:
        out["reason"] = "success_route_not_found"
        return out
    adj = {}
    for e in edges:
        adj.setdefault(e["from"], set()).add(e["to"])
    parent = {entry_pat: None}
    queue = deque([entry_pat])
    while queue:
        cur = queue.popleft()
        for nxt in sorted(adj.get(cur, ())):
            if nxt not in parent:
                parent[nxt] = cur
                queue.append(nxt)
    if success_pat not in parent:
        out.update(status="unreachable", reason="success_not_reachable_from_entry")
        out["frontier"] = sorted(parent)
        return out
    path, cur = [], success_pat
    while cur is not None:
        path.append(cur)
        cur = parent[cur]
    path.reverse()
    out["funnel"] = {"entry": entry_pat, "success": success_pat, "stages": [
        {"stage_key": p, "name": humanize(p), "order": i, "needs_gui_check": dynamic[p]} for i, p in enumerate(path)]}
    out.update(status="ok", reason=None)
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--entry", default="/")
    ap.add_argument("--success")
    a = ap.parse_args()
    print(json.dumps(extract(a.root, a.entry, a.success), ensure_ascii=False, indent=2))
