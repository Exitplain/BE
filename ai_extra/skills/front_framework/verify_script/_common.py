"""Shared helpers for ai_extra verify scripts. Exit codes: 0=pass, 1=fail, 2=blocked."""
import hashlib
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
FF = HERE.parent
REPO = FF.parents[2]
sys.path.insert(0, str(FF))

SOURCES = ("detect_framework.py", "extract_routes.py", "gen_fixture.py")
EXIT = {"pass": 0, "fail": 1, "blocked": 2}


def source_sha256():
    h = hashlib.sha256()
    for name in SOURCES:
        h.update((FF / name).read_bytes())
    return h.hexdigest()


def tmpdir(prefix):
    return Path(tempfile.mkdtemp(prefix=prefix))


def finish(script, requirements, verdict, reason, details, argv, out_dir=None, quiet=False):
    record = {"schema_version": "1.0", "script": script, "requirements": requirements, "verdict": verdict,
              "reason": reason, "details": details, "started_at": datetime.now(timezone.utc).isoformat(),
              "argv": argv, "python": sys.executable, "source_sha256": source_sha256()}
    out = Path(out_dir) if out_dir else REPO / "artifacts" / "ai_extra" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{Path(script).stem}.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    if not quiet:
        print(f"[{verdict.upper()}] {Path(script).name} {requirements} reason={reason} -> {path}")
    return EXIT[verdict], record


def add_common_args(ap):
    ap.add_argument("--out", help="directory for verdict json")
    ap.add_argument("--seeds", type=int, default=20)
    return ap
