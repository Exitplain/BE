"""Aggregate attempts without treating fixture performance as model accuracy."""
import argparse
import json
from collections import Counter
from pathlib import Path
from statistics import median


def aggregate(reports):
    sessions = [s for r in reports for s in r["sessions"]]
    statuses = Counter(s["status"] for s in sessions)
    n = len(sessions)
    return {"attempts": len(reports), "sessions": n,
        "statuses": dict(statuses), "success_rate": statuses["success"]/n if n else None,
        "agent_failure_rate": statuses["agent_failure"]/n if n else None,
        "median_session_seconds": median(s["duration_seconds"] for s in sessions) if n else None,
        "model_calls": sum(s["model_calls"] for s in sessions),
        "input_tokens": sum(s["input_tokens"] for s in sessions),
        "output_tokens": sum(s["output_tokens"] for s in sessions),
        "policy_labels": sorted({s.get("policy", "unspecified") for s in sessions}),
        "cost": None, "cost_completeness": "unavailable",
        "note": "Success is the configured URL predicate; scripted fixture results do not measure model or UX diagnostic accuracy."}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    reports = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(args.root.rglob("summary.json"))]
    print(json.dumps(aggregate(reports), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
