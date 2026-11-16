"""FUNNEL-01/02/06: extractor output vs ground truth (generated spec + hand-written fixtures)."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import FF, add_common_args, finish, tmpdir  # noqa: E402
from extract_routes import extract  # noqa: E402
from gen_fixture import FRAMEWORKS, generate  # noqa: E402


def stages(res):
    return [s["stage_key"] for s in res["funnel"]["stages"]] if res.get("funnel") else None


def check_generated(args):
    cases, failures = 0, []
    for fw in FRAMEWORKS:
        for seed in range(args.seeds):
            for pages, cut in ((3, None), (5, None), (8, None), (5, 1), (6, 2), (4, 0)):
                d = tmpdir(f"{fw}_{seed}_{pages}_")
                exp = generate(d, fw, seed, pages=pages, distractors=seed % 4, detour=bool(seed % 2), cut_at=cut)
                res = extract(d, exp["entry"], exp["success"])
                cases += 1
                problems = []
                if res["status"] != exp["status"]:
                    problems.append(f"status {res['status']} != {exp['status']}")
                if stages(res) != exp["funnel"]:
                    problems.append(f"funnel {stages(res)} != {exp['funnel']}")
                if res["framework"]["router"] != exp["router"]:
                    problems.append(f"router {res['framework']['router']} != {exp['router']}")
                if exp["status"] == "ok" and sorted(r["pattern"] for r in res["routes"]) != exp["routes"]:
                    problems.append("route set differs")
                if problems:
                    failures.append({"case": f"{fw}/seed{seed}/pages{pages}", "problems": problems})
    return cases, failures


def check_hand():
    cases, failures = 0, []
    base = FF / "fixtures" / "hand"
    for d in sorted(p for p in base.iterdir() if p.is_dir()):
        exp = json.loads((d / "expected.json").read_text("utf-8"))
        res = extract(d, exp["entry"], exp["success"])
        cases += 1
        problems = []
        if res["status"] != exp["status"]:
            problems.append(f"status {res['status']} != {exp['status']} ({res['reason']})")
        if exp.get("reason") and res["reason"] != exp["reason"]:
            problems.append(f"reason {res['reason']} != {exp['reason']}")
        if stages(res) != exp["funnel"]:
            problems.append(f"funnel {stages(res)} != {exp['funnel']}")
        if res["framework"]["router"] != exp["router"]:
            problems.append(f"router {res['framework']['router']} != {exp['router']}")
        flagged = sorted(s["stage_key"] for s in (res["funnel"] or {}).get("stages", []) if s["needs_gui_check"])
        if flagged != sorted(exp["needs_gui_check"]):
            problems.append(f"needs_gui_check {flagged} != {exp['needs_gui_check']}")
        kinds = sorted({u["kind"] for u in res["unresolved"]})
        if kinds != sorted(exp["unresolved_kinds"]):
            problems.append(f"unresolved kinds {kinds} != {exp['unresolved_kinds']}")
        if problems:
            failures.append({"case": f"hand/{d.name}", "problems": problems})
    return cases, failures


def main():
    ap = add_common_args(argparse.ArgumentParser(description=__doc__))
    ap.add_argument("--min-pass-rate", type=float, help="required; no default threshold is assumed")
    args = ap.parse_args()
    if args.min_pass_rate is None:
        code, _ = finish(__file__, ["FUNNEL-01", "FUNNEL-02", "FUNNEL-06"], "blocked", "threshold_undefined",
                         {}, sys.argv, args.out)
        sys.exit(code)
    gen_cases, gen_fail = check_generated(args)
    hand_cases, hand_fail = check_hand()
    total = gen_cases + hand_cases
    failures = gen_fail + hand_fail
    rate = (total - len(failures)) / total
    verdict = "pass" if rate >= args.min_pass_rate else "fail"
    code, _ = finish(__file__, ["FUNNEL-01", "FUNNEL-02", "FUNNEL-06"], verdict,
                     None if verdict == "pass" else "below_threshold",
                     {"cases": total, "failed": len(failures), "pass_rate": round(rate, 4),
                      "min_pass_rate": args.min_pass_rate, "failures": failures[:20]}, sys.argv, args.out)
    sys.exit(code)


if __name__ == "__main__":
    main()
