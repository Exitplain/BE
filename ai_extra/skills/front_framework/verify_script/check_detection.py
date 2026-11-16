"""FUNNEL-05: framework detection matches known fixtures and never guesses on ambiguous/unsupported trees."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import add_common_args, finish, tmpdir  # noqa: E402
from detect_framework import detect  # noqa: E402
from gen_fixture import FRAMEWORKS, ROUTERS, generate, write  # noqa: E402


def pkg(deps):
    return json.dumps({"dependencies": deps})


def negative_cases():
    cases = []

    def case(name, build, expect_status, expect_reason=None, expect_router=None):
        d = tmpdir(f"det_{name}_")
        build(d)
        cases.append((name, d, expect_status, expect_reason, expect_router))

    case("no_package_json", lambda d: write(d / "src/a.tsx", "export default 1;"), "blocked", "no_package_json")
    case("unsupported", lambda d: write(d / "package.json", pkg({"svelte": "^4.0.0"})), "blocked", "framework_unsupported")
    case("react_without_router", lambda d: write(d / "package.json", pkg({"react": "^18.0.0"})), "blocked", "no_router_detected")

    def ambiguous(d):
        write(d / "package.json", pkg({"next": "14.0.0", "react-router-dom": "^6.0.0"}))
        write(d / "app/page.tsx", "export default function P() { return null; }")
    case("ambiguous", ambiguous, "blocked", "framework_ambiguous")

    def next_empty(d):
        write(d / "package.json", pkg({"next": "14.0.0"}))
    case("next_without_routes", next_empty, "blocked", "no_routes_found")

    def next_mixed(d):
        write(d / "package.json", pkg({"next": "14.0.0"}))
        write(d / "app/page.tsx", "export default function P() { return null; }")
        write(d / "pages/old.tsx", "export default function P() { return null; }")
    case("next_mixed", next_mixed, "ok", None, "next-mixed")

    case("rr_v5", lambda d: write(d / "package.json", pkg({"react-router-dom": "^5.3.0"})), "ok", None, "react-router-v5")
    case("vue_router_v3", lambda d: write(d / "package.json", pkg({"vue-router": "^3.6.0"})), "ok", None, "vue-router-v3")
    return cases


def main():
    ap = add_common_args(argparse.ArgumentParser(description=__doc__))
    args = ap.parse_args()
    failures, total = [], 0
    for fw in FRAMEWORKS:
        for seed in range(args.seeds):
            d = tmpdir(f"det_{fw}_{seed}_")
            generate(d, fw, seed, pages=4)
            got = detect(d)
            total += 1
            if got["router"] != ROUTERS[fw]:
                failures.append({"case": f"{fw}/seed{seed}", "problem": f"router {got['router']} != {ROUTERS[fw]}"})
    for name, d, status, reason, router in negative_cases():
        got = detect(d)
        total += 1
        problems = []
        if got["status"] != status:
            problems.append(f"status {got['status']} != {status}")
        if reason and got["reason"] != reason:
            problems.append(f"reason {got['reason']} != {reason}")
        if router and got["router"] != router:
            problems.append(f"router {got['router']} != {router}")
        if status == "blocked" and got["framework"] and got["reason"] in {"framework_ambiguous", "framework_unsupported", "no_package_json"}:
            problems.append("guessed a framework while blocked")
        if problems:
            failures.append({"case": name, "problem": "; ".join(problems)})
    verdict = "pass" if not failures else "fail"
    code, _ = finish(__file__, ["FUNNEL-05"], verdict, None if not failures else "detection_mismatch",
                     {"cases": total, "failed": len(failures), "failures": failures[:20]}, sys.argv, args.out)
    sys.exit(code)


if __name__ == "__main__":
    main()
