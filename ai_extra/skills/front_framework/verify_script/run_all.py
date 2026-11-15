"""Run every front_framework verify script and aggregate. Exit: 1 if any fail, else 2 if any blocked, else 0."""
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import HERE, REPO, source_sha256  # noqa: E402

SCRIPTS = [
    ("check_no_hardcoding.py", []),
    ("check_detection.py", []),
    ("compare_extraction.py", ["--min-pass-rate", "1.0"]),
    ("check_metamorphic.py", []),
    ("check_evidence_locations.py", []),
]


def main():
    out = REPO / "artifacts" / "ai_extra" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    seeds = ["--seeds", sys.argv[1]] if len(sys.argv) > 1 else []
    results = []
    for name, extra in SCRIPTS:
        argv = [sys.executable, "-I", str(HERE / name), "--out", str(out)] + extra + (seeds if name != "check_no_hardcoding.py" else [])
        proc = subprocess.run(argv, capture_output=True, text=True)
        verdict = {0: "pass", 1: "fail", 2: "blocked"}.get(proc.returncode, "fail")
        results.append({"script": name, "verdict": verdict, "exit": proc.returncode,
                        "stdout": proc.stdout.strip(), "stderr": proc.stderr.strip()[-400:]})
        print(proc.stdout.strip() or f"[{verdict.upper()}] {name} (no output) {proc.stderr.strip()[-200:]}")
    overall = "fail" if any(r["verdict"] == "fail" for r in results) else ("blocked" if any(r["verdict"] == "blocked" for r in results) else "pass")
    (out / "verdict.json").write_text(json.dumps({"schema_version": "1.0", "overall": overall, "python": sys.executable,
                                                  "source_sha256": source_sha256(), "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OVERALL {overall.upper()} -> {out / 'verdict.json'}")
    sys.exit({"pass": 0, "fail": 1, "blocked": 2}[overall])


if __name__ == "__main__":
    main()
