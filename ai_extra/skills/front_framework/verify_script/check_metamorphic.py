"""FUNNEL-06/13: relations that must hold regardless of names - rename, add page, cut link, noise pages."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import add_common_args, finish, tmpdir  # noqa: E402
from extract_routes import extract  # noqa: E402
from gen_fixture import FRAMEWORKS, generate  # noqa: E402

ROUTED = [f for f in FRAMEWORKS if f != "spa_state"]


def run(fw, seed, **kw):
    d = tmpdir(f"meta_{fw}_{seed}_")
    exp = generate(d, fw, seed, **kw)
    res = extract(d, exp["entry"], exp["success"])
    keys = [s["stage_key"] for s in res["funnel"]["stages"]] if res["funnel"] else None
    return exp, res, keys


def main():
    ap = add_common_args(argparse.ArgumentParser(description=__doc__))
    args = ap.parse_args()
    failures, total = [], 0

    def expect(cond, case, msg):
        nonlocal total
        total += 1
        if not cond:
            failures.append({"case": case, "problem": msg})

    for fw in ROUTED:
        for seed in range(args.seeds):
            case = f"{fw}/seed{seed}"
            _, _, base = run(fw, seed, pages=5)
            _, _, renamed = run(fw, seed, pages=5, name_seed=seed + 1000)
            expect(base is not None and renamed is not None and len(base) == len(renamed) == 5, case, f"rename changed length {base} {renamed}")
            expect(base != renamed, case, "renamed fixture produced identical stage keys")
            _, _, more = run(fw, seed, pages=6)
            expect(more is not None and len(more) == 6, case, f"adding a page gave {len(more) if more else None} stages")
            _, _, quiet = run(fw, seed, pages=5, distractors=0, detour=False)
            _, _, noisy = run(fw, seed, pages=5, distractors=3, detour=True)
            expect(quiet is not None and noisy is not None and len(quiet) == len(noisy) == 5, case, "noise pages changed funnel length")
            # no alternative path: cutting a link must make the success page unreachable
            exp_cut, res_cut, _ = run(fw, seed, pages=5, cut_at=2, detour=False)
            expect(res_cut["status"] == "unreachable" and res_cut["funnel"] is None, case,
                   f"cut link gave status {res_cut['status']}")
            expect(exp_cut["success"] not in res_cut.get("frontier", []), case, "unreachable success listed in frontier")
            # a detour rejoins the main path: the funnel must reroute and get longer, not disappear
            exp_alt, res_alt, alt = run(fw, seed, pages=5, cut_at=2, detour=True)
            expect(alt is not None and alt == exp_alt["funnel"] and len(alt) > 5, case,
                   f"detour reroute {alt} != {exp_alt['funnel']}")
    verdict = "pass" if not failures else "fail"
    code, _ = finish(__file__, ["FUNNEL-06", "FUNNEL-13"], verdict, None if not failures else "relation_violated",
                     {"checks": total, "failed": len(failures), "failures": failures[:20]}, sys.argv, args.out)
    sys.exit(code)


if __name__ == "__main__":
    main()
