"""Static guard: the extractor and detector must not embed funnel/stage vocabulary.

Includes a self test (the guard must flag a deliberately hardcoded dictionary), otherwise a
broken guard could pass silently.
"""
import argparse
import ast
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import FF, add_common_args, finish  # noqa: E402

VOCAB = {"landing", "product", "cart", "signup", "checkout", "complete", "delivery", "benefits", "coupon",
         "account", "subscription", "home", "basket", "order", "payment", "login", "shop", "pricing"}
# "success" is not listed: success_rule / success_route are contract terms, not a stage name.
# "order" is the exact schema key of stages[].order; only that exact string is allowed.
ALLOW_EXACT = {"order"}
TARGETS = ("detect_framework.py", "extract_routes.py")


def scan_source(source):
    hits = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value not in ALLOW_EXACT:
            for word in re.findall(r"[a-zA-Z]+", node.value.lower()):
                if word in VOCAB:
                    hits.append({"line": node.lineno, "word": word, "text": node.value[:60]})
    return hits


def main():
    ap = add_common_args(argparse.ArgumentParser(description=__doc__))
    args = ap.parse_args()
    probe = scan_source('STAGES = {"/cart": "cart", "/checkout": "checkout"}')
    if len(probe) < 4:
        code, _ = finish(__file__, ["FUNNEL-00", "FUNNEL-06"], "fail", "guard_self_test_failed",
                         {"probe_hits": probe}, sys.argv, args.out)
        sys.exit(code)
    findings = {}
    for name in TARGETS:
        hits = scan_source((FF / name).read_text("utf-8"))
        if hits:
            findings[name] = hits
    verdict = "pass" if not findings else "fail"
    code, _ = finish(__file__, ["FUNNEL-00", "FUNNEL-06"], verdict, None if not findings else "vocabulary_found",
                     {"files": list(TARGETS), "findings": findings, "vocabulary_size": len(VOCAB)}, sys.argv, args.out)
    sys.exit(code)


if __name__ == "__main__":
    main()
