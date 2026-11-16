"""Map extracted routes onto the FIXED 7 funnel stages of Figma P12 / agent/funnel.py.

No stage-name vocabulary lives here. Stage keys come from stage_roles.json (the contract) and each route is
assigned by STRUCTURAL signals read from the target's code:
  entry route -> entry stage        success route -> success stage
  password input in the page -> signup          form with >= N inputs and no password -> payment
  dynamic (param) route -> detail               static page linking to a dynamic route -> browse
Pages on the funnel path without a signal take their stage from path order (basis="ordinal"), never below the
previous page's stage. Anything else stays unmapped. Result is unverified until a GUI check confirms it.
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "skills" / "front_framework"))
from extract_routes import extract  # noqa: E402

PASSWORD = re.compile(r"""type\s*=\s*['"]password['"]""", re.I)
INPUT = re.compile(r"<(?:input|textarea|select)\b", re.I)
PARAM = re.compile(r":\w|\[|\*")


def load_roles(path=None):
    return json.loads(Path(path or HERE / "stage_roles.json").read_text("utf-8"))


def static_prefix(pattern):
    segs = []
    for seg in pattern.strip("/").split("/"):
        if not seg or PARAM.search(seg):
            break
        segs.append(seg)
    return "/" + "/".join(segs)


def param_pattern(pattern):
    """'/item/[id]' -> '/item/:id' (the notation stage_paths would need for dynamic routes)."""
    return re.sub(r"\[\.\.\.(\w+)\]", r":\1*", re.sub(r"\[(\w+)\]", r":\1", pattern))


def signals(root, files):
    text = "".join((Path(root) / f).read_text("utf-8", errors="replace") for f in files if (Path(root) / f).is_file())
    inputs = len(INPUT.findall(text))
    return {"password": bool(PASSWORD.search(text)), "inputs": inputs}


def build(extraction, root, roles):
    result = {"status": "blocked", "reason": None, "stage_paths": {}, "stage_patterns": {}, "details": [],
              "collisions": [], "unmapped": [], "verification": "unverified"}
    if extraction["status"] != "ok" or not extraction.get("funnel"):
        result["reason"] = extraction.get("reason") or extraction["status"]
        return result
    order = roles["order"]
    entry, success = extraction["funnel"]["entry"], extraction["funnel"]["success"]
    path_order = [s["stage_key"] for s in extraction["funnel"]["stages"]]
    routes = {r["pattern"]: r for r in extraction["routes"]}
    dynamic = {p for p in routes if PARAM.search(p)}
    to_dynamic = {e["from"] for e in extraction["edges"] if e["to"] in dynamic and e["from"] not in dynamic}
    details = {}
    for pat, r in routes.items():
        sig = signals(root, r.get("files") or [])
        if pat == entry:
            key, basis = roles["entry"], "entry"
        elif pat == success:
            key, basis = roles["success"], "success"
        elif sig["password"]:
            key, basis = roles["signup"], "signal:password_input"
        elif sig["inputs"] >= roles["min_payment_inputs"]:
            key, basis = roles["payment"], "signal:form_inputs"
        elif pat in dynamic:
            key, basis = roles["detail"], "signal:dynamic_route"
        elif pat in to_dynamic:
            key, basis = roles["browse"], "signal:links_to_dynamic_route"
        else:
            key, basis = None, None
        details[pat] = {"pattern": pat, "key": key, "basis": basis, "signals": sig}
    prev = None
    for pat in path_order:  # monotone fill along the funnel path for pages without a signal
        d = details[pat]
        if d["key"] is None:
            nxt = order[order.index(roles["entry"]) + 1] if prev in (None, roles["entry"]) else prev
            d["key"], d["basis"] = nxt, "ordinal"
        prev = d["key"]
    last = -1
    for pat in path_order:
        idx = order.index(details[pat]["key"])
        details[pat]["order_violation"] = idx < last
        last = max(last, idx)
    for pat, d in details.items():
        if d["key"] is None:
            result["unmapped"].append(pat)
            continue
        result["stage_patterns"][param_pattern(pat)] = d["key"]
        prefix = static_prefix(pat)
        if PARAM.search(pat):
            result["collisions"].append({"pattern": pat, "reason": "dynamic route: not expressible as a path prefix in the current stage_paths"})
        elif prefix in result["stage_paths"] and result["stage_paths"][prefix] != d["key"]:
            result["collisions"].append({"pattern": pat, "reason": "prefix collision"})
        else:
            result["stage_paths"][prefix] = d["key"]
    result["details"] = [details[p] for p in path_order] + [d for p, d in details.items() if p not in path_order]
    result.update(status="ok", reason=None)
    return result


def run(root, entry, success, roles=None):
    return build(extract(root, entry, success), Path(root).resolve(), roles or load_roles())


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--entry", default="/")
    ap.add_argument("--success", required=True)
    a = ap.parse_args()
    print(json.dumps(run(a.root, a.entry, a.success), ensure_ascii=False, indent=2))
