"""FUNNEL-02: every extracted route/edge points at a real file:line that contains the claimed construct."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import FF, add_common_args, finish, tmpdir  # noqa: E402
from extract_routes import extract  # noqa: E402
from gen_fixture import FRAMEWORKS, generate  # noqa: E402

FILE_BASED = {"next_app", "next_pages"}


def line_text(root, rel, line):
    path = Path(root) / rel
    if not path.is_file():
        return None
    lines = path.read_text("utf-8", errors="replace").splitlines()
    return lines[line - 1] if 1 <= line <= len(lines) else None


def verify(root, res, file_based, label, failures):
    n = 0
    for r in res["routes"]:
        n += 1
        text = line_text(root, r["file"], r["line"])
        if text is None:
            failures.append({"case": label, "problem": f"route file/line missing {r['file']}:{r['line']}"})
        elif not file_based and r["pattern"] not in text:
            failures.append({"case": label, "problem": f"route {r['pattern']} not on {r['file']}:{r['line']}"})
    for e in res["edges"] + res["global_edges"]:
        n += 1
        text = line_text(root, e["file"], e["line"])
        if text is None:
            failures.append({"case": label, "problem": f"edge file/line missing {e['file']}:{e['line']}"})
        elif e["to"] not in text:
            failures.append({"case": label, "problem": f"edge target {e['to']} not on {e['file']}:{e['line']}"})
    return n


def main():
    ap = add_common_args(argparse.ArgumentParser(description=__doc__))
    args = ap.parse_args()
    failures, checked = [], 0
    for fw in FRAMEWORKS:
        if fw == "spa_state":
            continue
        for seed in range(args.seeds):
            d = tmpdir(f"ev_{fw}_{seed}_")
            exp = generate(d, fw, seed, pages=5)
            res = extract(d, exp["entry"], exp["success"])
            checked += verify(d, res, fw in FILE_BASED, f"{fw}/seed{seed}", failures)
    verdict = "pass" if not failures else "fail"
    code, _ = finish(__file__, ["FUNNEL-02"], verdict, None if not failures else "bad_evidence_location",
                     {"locations_checked": checked, "failed": len(failures), "failures": failures[:20]}, sys.argv, args.out)
    sys.exit(code)


if __name__ == "__main__":
    main()
