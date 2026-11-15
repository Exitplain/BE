"""Integration check: extractor -> 7 fixed stages -> develop's real agent/funnel.py.

Loads the project's own agent/funnel.py by file path (no package import), so a rename of a stage key or a change of
the matching rule on develop is detected here. Exit codes: 0 pass, 1 fail, 2 blocked.
"""
import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
INTEG = HERE.parent
REPO = INTEG.parents[1]
sys.path.insert(0, str(INTEG))
sys.path.insert(0, str(INTEG.parent / "skills" / "front_framework" / "verify_script"))
from _common import finish, tmpdir  # noqa: E402
from gen_fixture import FRAMEWORKS, generate  # noqa: E402
from to_stage_paths import PARAM, load_roles, run  # noqa: E402


def load_funnel(path):
    spec = importlib.util.spec_from_file_location("develop_funnel", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def proposed_match(url, table):
    """Reference for the PROPOSED pattern-aware matching: same prefix rule as develop's funnel_stage, but ':id'
    segments match any one segment. The most specific pattern (longest, then most static segments) wins."""
    segs = [x for x in url.split("?")[0].strip("/").split("/") if x]
    best = None
    for pat, key in table.items():
        ps = [x for x in pat.strip("/").split("/") if x]
        if len(ps) > len(segs) or (not ps and segs):  # "/" only matches exactly "/"
            continue
        if not all(p.startswith(":") or p == s for p, s in zip(ps, segs)):
            continue
        score = (len(ps), sum(1 for p in ps if not p.startswith(":")))
        if best is None or score > best[0]:
            best = (score, key)
    return best[1] if best else None


def sample_url(pattern):
    return re.sub(r"\[\.\.\.\w+\]|\[\w+\]|:\w+\*?", "7", pattern)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--develop-root", default=str(REPO))
    ap.add_argument("--seeds", type=int, default=15)
    ap.add_argument("--out")
    a = ap.parse_args()
    funnel_path = Path(a.develop_root) / "agent" / "funnel.py"
    if not funnel_path.is_file():
        code, _ = finish(__file__, ["FUNNEL-INTEG"], "blocked", "develop_funnel_not_found", {"path": str(funnel_path)}, sys.argv, a.out)
        sys.exit(code)
    dev = load_funnel(funnel_path)
    roles = load_roles()
    if list(dev.FUNNEL_KEYS) != roles["order"]:
        code, _ = finish(__file__, ["FUNNEL-INTEG"], "blocked", "stage_keys_out_of_sync",
                         {"develop": list(dev.FUNNEL_KEYS), "stage_roles.json": roles["order"]}, sys.argv, a.out)
        sys.exit(code)
    failures, notes, cases = [], {"dynamic_not_expressible_now": 0, "dynamic_flagged_as_collision": 0}, 0

    def check(cond, case, msg):
        nonlocal cases
        cases += 1
        if not cond:
            failures.append({"case": case, "problem": msg})

    # ---- 1. hand-written shop-like fixture with structural signals
    fx = INTEG / "fixtures" / "shopflow_next"
    exp = json.loads((fx / "expected.json").read_text("utf-8"))
    res = run(fx, exp["entry"], exp["success"], roles)
    got_keys = {d["pattern"].replace("[id]", ":id"): d["key"] for d in res["details"]}
    check(got_keys == exp["expected_keys"], "shopflow/keys", "%s != %s" % (got_keys, exp["expected_keys"]))
    check(res["stage_paths"] == exp["expected_stage_paths"], "shopflow/stage_paths", "%s != %s" % (res["stage_paths"], exp["expected_stage_paths"]))
    check(sorted(c["pattern"] for c in res["collisions"]) == sorted(exp["expected_collisions"]), "shopflow/collisions", str(res["collisions"]))
    # ---- 2. random-name fixtures: no signals, only structure and order
    for fw in FRAMEWORKS:
        if fw == "spa_state":
            continue
        for seed in range(a.seeds):
            d = tmpdir("integ_%s_%d_" % (fw, seed))
            spec = generate(d, fw, seed, pages=5)
            r1 = run(d, spec["entry"], spec["success"], roles)
            r2 = run(d, spec["entry"], spec["success"], roles)
            case = "%s/seed%d" % (fw, seed)
            check(r1["status"] == "ok", case, "adapter status %s %s" % (r1["status"], r1["reason"]))
            if r1["status"] != "ok":
                continue
            check(r1 == r2, case, "not deterministic")
            by = {x["pattern"]: x for x in r1["details"]}
            check(by[spec["entry"]]["key"] == roles["entry"] and by[spec["success"]]["key"] == roles["success"], case, "entry/success anchors")
            path = spec["funnel"]
            idx = [roles["order"].index(by[p]["key"]) for p in path]
            check(idx == sorted(idx), case, "stage order not monotone along the path: %s" % idx)
            check(all(by[p]["key"] in dev.FUNNEL_KEYS for p in path), case, "key outside the 7 stages")
            check(len(set(by[p]["key"] for p in path)) <= 7, case, "more than 7 stages")
    # ---- 3. contract + round trip through develop's real funnel_stage
    tables = [("shopflow", res, exp["success"])]
    for fw in ("react_router", "next_app", "next_pages", "vue_router"):
        for seed in range(a.seeds):
            d = tmpdir("integ2_%s_%d_" % (fw, seed))
            spec = generate(d, fw, seed, pages=6)
            tables.append(("%s/seed%d" % (fw, seed), run(d, spec["entry"], spec["success"], roles), spec["success"]))
    for name, r, success in tables:
        for pth, key in r["stage_paths"].items():  # same condition as RunInput.stage_paths validation on develop
            check(pth.startswith("/") and key in dev.FUNNEL_KEYS and not PARAM.search(pth), name, "invalid stage_paths entry %s -> %s" % (pth, key))
            got = dev.funnel_stage(pth, success, r["stage_paths"])
            check(got == key, name, "funnel_stage(%s) = %s, expected %s" % (pth, got, key))
        for dd in r["details"]:  # no silent wrongness: a dynamic route is either flagged or answered correctly
            if PARAM.search(dd["pattern"]) and dd["key"]:
                got = dev.funnel_stage(sample_url(dd["pattern"]), success, r["stage_paths"])
                flagged = any(c["pattern"] == dd["pattern"] for c in r["collisions"])
                if got != dd["key"]:
                    notes["dynamic_not_expressible_now"] += 1
                notes["dynamic_flagged_as_collision"] += int(flagged)
                check(got == dd["key"] or flagged, name, "dynamic route %s silently mis-staged (%s)" % (dd["pattern"], got))
        for pth, key in r["stage_patterns"].items():  # proposed pattern-aware matching can express everything
            got = proposed_match(sample_url(pth), r["stage_patterns"])
            check(got == key, name, "proposed matcher: %s -> %s, expected %s" % (pth, got, key))
    verdict = "pass" if not failures else "fail"
    code, _ = finish(__file__, ["FUNNEL-INTEG"], verdict, None if not failures else "contract_or_roundtrip_mismatch",
                     {"checks": cases, "failed": len(failures), "failures": failures[:15], "notes": notes,
                      "develop_funnel": str(funnel_path), "stage_keys": list(dev.FUNNEL_KEYS)}, sys.argv, a.out)
    sys.exit(code)


if __name__ == "__main__":
    main()
