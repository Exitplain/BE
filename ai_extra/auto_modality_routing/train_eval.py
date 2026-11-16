"""Train and evaluate the modality classifier on held-out splits (Python 3.8).

Splits: train / val (early stopping only) / test_seen (new samples of trained templates) /
test_unseen (templates never used for training) / ood (blank, solid, ... -> must abstain).
Thresholds are PRE-REGISTERED in README.md and passed in explicitly; none are tuned on the test splits.
Exit code: 0 pass, 1 fail, 2 blocked.
"""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image
from sklearn.dummy import DummyClassifier
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score
from sklearn.tree import DecisionTreeClassifier

sys.path.insert(0, str(Path(__file__).resolve().parent))
from features import featurize  # noqa: E402

CLASSES = ["canvas", "image", "media_ui", "text"]
SPLITS = ["train", "val", "test_seen", "test_unseen", "ood"]


def load(data, split):
    meta = json.loads((Path(data) / split / "meta.json").read_text("utf-8"))
    rows, names, files, labels, templates, hashes = [], None, [], [], [], []
    for m in meta:
        png = Path(data) / split / (m["file"] + ".png")
        if not png.exists():
            continue
        f = featurize(png)
        names = names or sorted(f)
        rows.append([f[k] for k in names])
        files.append(m["file"])
        labels.append(m["label"])
        templates.append(m["template"])
        small = np.asarray(Image.open(png).convert("L").resize((100, 80)), dtype=np.uint8)
        hashes.append(hashlib.sha256(small.tobytes()).hexdigest())
    return {"X": np.array(rows), "y": np.array(labels), "t": np.array(templates), "h": hashes, "names": names, "n_meta": len(meta)}


def prf(y, p):
    return {"accuracy": round(float(accuracy_score(y, p)), 4),
            "macro_f1": round(float(f1_score(y, p, labels=CLASSES, average="macro", zero_division=0)), 4)}


def abstain_stats(proba, tau):
    return float((proba.max(axis=1) < tau).mean())


def fit_catboost(X, y, Xv, yv):
    from catboost import CatBoostClassifier
    m = CatBoostClassifier(iterations=400, depth=4, learning_rate=0.1, random_seed=0, verbose=0, thread_count=2,
                           loss_function="MultiClass", classes_count=4)
    m.fit(X, [CLASSES.index(c) for c in y], eval_set=(Xv, [CLASSES.index(c) for c in yv]), early_stopping_rounds=40)
    return m


def predict_proba(model, X, kind):
    if kind == "catboost":
        return model.predict_proba(X)
    proba = model.predict_proba(X)
    full = np.zeros((len(X), len(CLASSES)))
    for i, c in enumerate(model.classes_):
        full[:, CLASSES.index(c)] = proba[:, i]
    return full


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("data")
    ap.add_argument("--out", default="results.json")
    ap.add_argument("--tau", type=float, default=0.6)
    for name in ("seen_f1", "unseen_f1", "margin_over_tree", "ood_abstain", "seen_abstain_max"):
        ap.add_argument("--min-" + name.replace("_", "-") if not name.endswith("max") else "--max-seen-abstain", type=float, dest=name)
    a = ap.parse_args()
    t0 = time.time()
    missing = [s for s in SPLITS if not (Path(a.data) / s / "meta.json").exists()]
    if missing or None in (a.seen_f1, a.unseen_f1, a.margin_over_tree, a.ood_abstain, a.seen_abstain_max):
        print("BLOCKED", "missing splits %s" % missing if missing else "thresholds not provided")
        sys.exit(2)
    d = {s: load(a.data, s) for s in SPLITS}
    short = {s: (d[s]["n_meta"], len(d[s]["y"])) for s in SPLITS if d[s]["n_meta"] != len(d[s]["y"])}
    if short:
        print("BLOCKED", "renders missing", short)
        sys.exit(2)
    names = d["train"]["names"]
    # ---- split leakage
    train_hashes = set(d["train"]["h"])
    leaks = {s: sum(h in train_hashes for h in d[s]["h"]) for s in SPLITS if s != "train"}
    X, y = d["train"]["X"], d["train"]["y"]
    models = {"majority": DummyClassifier(strategy="most_frequent").fit(X, y),
              "tree_depth3": DecisionTreeClassifier(max_depth=3, random_state=0).fit(X, y)}
    cb = fit_catboost(X, y, d["val"]["X"], d["val"]["y"])
    results = {"data": a.data, "tau": a.tau, "n": {s: len(d[s]["y"]) for s in SPLITS}, "split_hash_leaks_vs_train": leaks, "models": {}}
    for mname in ("majority", "tree_depth3", "catboost"):
        kind = "catboost" if mname == "catboost" else "sk"
        model = cb if mname == "catboost" else models[mname]
        r = {}
        for s in ("val", "test_seen", "test_unseen"):
            proba = predict_proba(model, d[s]["X"], kind)
            pred = np.array([CLASSES[i] for i in proba.argmax(axis=1)])
            r[s] = prf(d[s]["y"], pred)
            r[s]["confusion"] = confusion_matrix(d[s]["y"], pred, labels=CLASSES).tolist()
            r[s]["per_template_accuracy"] = {t: round(float((pred[d[s]["t"] == t] == d[s]["y"][d[s]["t"] == t]).mean()), 3) for t in sorted(set(d[s]["t"]))}
            r[s]["abstain_rate"] = round(abstain_stats(proba, a.tau), 4)
            keep = proba.max(axis=1) >= a.tau
            r[s]["selective_accuracy"] = round(float((pred[keep] == d[s]["y"][keep]).mean()), 4) if keep.any() else None
        r["ood_abstain_rate"] = round(abstain_stats(predict_proba(model, d["ood"]["X"], kind), a.tau), 4)
        r["ood_predicted"] = {c: int(v) for c, v in zip(*np.unique([CLASSES[i] for i in predict_proba(model, d["ood"]["X"], kind).argmax(axis=1)], return_counts=True))}
        results["models"][mname] = r
    imp = sorted(zip(names, cb.get_feature_importance()), key=lambda x: -x[1])[:8]
    results["catboost_top_features"] = [[n, round(float(v), 2)] for n, v in imp]
    cb2 = fit_catboost(X, y, d["val"]["X"], d["val"]["y"])
    same = bool(np.array_equal(cb.predict_proba(d["test_unseen"]["X"]), cb2.predict_proba(d["test_unseen"]["X"])))
    results["catboost_deterministic_retrain"] = same
    c = results["models"]["catboost"]
    t = results["models"]["tree_depth3"]
    checks = {
        "seen_macro_f1": (c["test_seen"]["macro_f1"], ">=", a.seen_f1),
        "unseen_macro_f1": (c["test_unseen"]["macro_f1"], ">=", a.unseen_f1),
        "unseen_margin_over_tree": (round(c["test_unseen"]["macro_f1"] - t["test_unseen"]["macro_f1"], 4), ">=", a.margin_over_tree),
        "ood_abstain": (c["ood_abstain_rate"], ">=", a.ood_abstain),
        "seen_abstain": (c["test_seen"]["abstain_rate"], "<=", a.seen_abstain_max),
        "deterministic_retrain": (same, "==", True),
        "no_hash_leaks": (sum(leaks.values()), "==", 0),
    }
    verdicts = {}
    for k, (val, op, thr) in checks.items():
        ok = {">=": val >= thr, "<=": val <= thr, "==": val == thr}[op]
        verdicts[k] = {"value": val, "op": op, "threshold": thr, "pass": bool(ok)}
    results["checks"] = verdicts
    results["verdict"] = "pass" if all(v["pass"] for v in verdicts.values()) else "fail"
    results["seconds"] = round(time.time() - t0, 1)
    Path(a.out).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print("verdict:", results["verdict"])
    for k, v in verdicts.items():
        print("  %-26s %s %s %s -> %s" % (k, v["value"], v["op"], v["threshold"], "pass" if v["pass"] else "FAIL"))
    for mname, r in results["models"].items():
        print("  [%s] seen F1 %.3f | unseen F1 %.3f | ood abstain %.2f | seen abstain %.2f" % (
            mname, r["test_seen"]["macro_f1"], r["test_unseen"]["macro_f1"], r["ood_abstain_rate"], r["test_seen"]["abstain_rate"]))
    sys.exit(0 if results["verdict"] == "pass" else 1)


if __name__ == "__main__":
    main()
