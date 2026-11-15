"""FUNNEL-01 on real third-party apps: extractor vs hand-read ground truth (route set and page-to-page edges).

Apps are NOT vendored. Check them out first (see fixtures/external_expected.json sources) and pass --apps-root
pointing at the checkout of each source. Static read only: nothing from the apps is installed or executed.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import FF, finish  # noqa: E402
from extract_routes import extract  # noqa: E402


def prf(expected, got):
    expected, got = set(expected), set(got)
    tp = len(expected & got)
    precision = tp / len(got) if got else (1.0 if not expected else 0.0)
    recall = tp / len(expected) if expected else 1.0
    return precision, recall


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apps-root", action="append", default=[], help="name=path of a source checkout, repeatable")
    ap.add_argument("--split", choices=("dev", "reserve", "all"), default="all")
    ap.add_argument("--min-route-recall", type=float)
    ap.add_argument("--min-edge-precision", type=float)
    ap.add_argument("--min-edge-recall", type=float)
    ap.add_argument("--out")
    args = ap.parse_args()
    if None in (args.min_route_recall, args.min_edge_precision, args.min_edge_recall):
        code, _ = finish(__file__, ["FUNNEL-01"], "blocked", "threshold_undefined", {}, sys.argv, args.out)
        sys.exit(code)
    roots = dict(item.split("=", 1) for item in args.apps_root)
    spec = json.loads((FF / "fixtures" / "external_expected.json").read_text("utf-8"))
    rows, missing = [], []
    for name, exp in spec["apps"].items():
        if args.split != "all" and exp["split"] != args.split:
            continue
        base = roots.get(exp["source"])
        app_dir = Path(base) / exp.get("subdir", "") / name if base else None
        if not app_dir or not app_dir.is_dir():
            missing.append(name)
            continue
        res = extract(app_dir, "/", exp.get("success"))
        got_routes = [r["pattern"] for r in res["routes"]]
        got_edges = {(e["from"], e["to"]) for e in res["edges"]}
        exp_edges = {tuple(e) for e in exp["page_edges"]}
        _, route_recall = prf(exp["routes"], got_routes)
        route_precision, _ = prf(exp["routes"], got_routes)
        edge_precision, edge_recall = prf(exp_edges, got_edges)
        rows.append({"app": name, "split": exp["split"], "route_recall": round(route_recall, 3),
                     "route_precision": round(route_precision, 3), "edge_precision": round(edge_precision, 3),
                     "edge_recall": round(edge_recall, 3),
                     "missing_routes": sorted(set(exp["routes"]) - set(got_routes)),
                     "extra_routes": sorted(set(got_routes) - set(exp["routes"])),
                     "missing_edges": sorted(map(list, exp_edges - got_edges)),
                     "extra_edges": sorted(map(list, got_edges - exp_edges)),
                     "status": res["status"], "reason": res["reason"]})
    if missing and not rows:
        code, _ = finish(__file__, ["FUNNEL-01"], "blocked", "apps_not_checked_out", {"missing": missing}, sys.argv, args.out)
        sys.exit(code)

    def mean(key):
        return sum(r[key] for r in rows) / len(rows)

    summary = {"apps": len(rows), "skipped_not_checked_out": missing, "route_recall": round(mean("route_recall"), 3),
               "route_precision": round(mean("route_precision"), 3), "edge_precision": round(mean("edge_precision"), 3),
               "edge_recall": round(mean("edge_recall"), 3)}
    ok = (summary["route_recall"] >= args.min_route_recall and summary["edge_precision"] >= args.min_edge_precision
          and summary["edge_recall"] >= args.min_edge_recall)
    code, _ = finish(__file__, ["FUNNEL-01"], "pass" if ok else "fail", None if ok else "below_threshold",
                     {"split": args.split, "summary": summary, "thresholds": {
                         "min_route_recall": args.min_route_recall, "min_edge_precision": args.min_edge_precision,
                         "min_edge_recall": args.min_edge_recall}, "per_app": rows}, sys.argv, args.out)
    for r in rows:
        flag = "ok " if r["route_recall"] == 1 and r["route_precision"] == 1 and r["edge_precision"] == 1 and r["edge_recall"] == 1 else "DIFF"
        print(f"  {flag} {r['app']:22} routes P{r['route_precision']:.2f}/R{r['route_recall']:.2f} edges P{r['edge_precision']:.2f}/R{r['edge_recall']:.2f}"
              + (f" missing_routes={r['missing_routes']}" if r["missing_routes"] else "")
              + (f" extra_routes={r['extra_routes']}" if r["extra_routes"] else "")
              + (f" missing_edges={r['missing_edges']}" if r["missing_edges"] else "")
              + (f" extra_edges={r['extra_edges']}" if r["extra_edges"] else ""))
    sys.exit(code)


if __name__ == "__main__":
    main()
