"""ROC / AUC and diagnostic plots for the modality classifier (same data, split and seed as train_eval.py)."""
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from sklearn.metrics import auc, confusion_matrix, roc_auc_score, roc_curve  # noqa: E402
from sklearn.tree import DecisionTreeClassifier  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from train_eval import CLASSES, SPLITS, fit_catboost, load, predict_proba  # noqa: E402

COLORS = {"canvas": "#d95f02", "image": "#1b9e77", "media_ui": "#7570b3", "text": "#e7298a"}


def onehot(y):
    return np.array([[c == v for c in CLASSES] for v in y], dtype=int)


def ovr_auc(y, proba):
    Y = onehot(y)
    per = {}
    for i, c in enumerate(CLASSES):
        per[c] = float(roc_auc_score(Y[:, i], proba[:, i])) if 0 < Y[:, i].sum() < len(Y) else None
    return per, float(np.mean([v for v in per.values() if v is not None]))


def main():
    data, outdir = sys.argv[1], Path(sys.argv[2])
    outdir.mkdir(parents=True, exist_ok=True)
    d = {s: load(data, s) for s in SPLITS}
    X, y = d["train"]["X"], d["train"]["y"]
    cb = fit_catboost(X, y, d["val"]["X"], d["val"]["y"])
    tree = DecisionTreeClassifier(max_depth=3, random_state=0).fit(X, y)
    P = {"catboost": {s: predict_proba(cb, d[s]["X"], "catboost") for s in SPLITS},
         "tree_depth3": {s: predict_proba(tree, d[s]["X"], "sk") for s in SPLITS}}
    summary = {}
    for m in P:
        summary[m] = {}
        for s in ("test_seen", "test_unseen"):
            per, macro = ovr_auc(d[s]["y"], P[m][s])
            summary[m][s] = {"per_class_auc": {k: (round(v, 3) if v is not None else None) for k, v in per.items()}, "macro_auc": round(macro, 3)}
    # per-template one-vs-rest AUC on unseen (which layouts break the ranking)
    templ = {}
    for t in sorted(set(d["test_unseen"]["t"])):
        idx = d["test_unseen"]["t"] == t
        cls = d["test_unseen"]["y"][idx][0]
        i = CLASSES.index(cls)
        yy = (d["test_unseen"]["y"] == cls).astype(int)
        # this template's samples against every OTHER class's samples
        keep = idx | (d["test_unseen"]["y"] != cls)
        templ[t] = {"class": cls, "auc_vs_other_classes": round(float(roc_auc_score(yy[keep], P["catboost"]["test_unseen"][keep, i])), 3)}
    summary["catboost_unseen_template_auc"] = templ

    # ---- figure 1: ROC curves
    fig, axes = plt.subplots(1, 2, figsize=(12, 5.2))
    for ax, s, title in zip(axes, ("test_seen", "test_unseen"), ("Seen templates (new samples)", "UNSEEN templates (never trained)")):
        Y = onehot(d[s]["y"])
        for i, c in enumerate(CLASSES):
            fpr, tpr, _ = roc_curve(Y[:, i], P["catboost"][s][:, i])
            ax.plot(fpr, tpr, color=COLORS[c], lw=2, label="%s  AUC=%.3f" % (c, auc(fpr, tpr)))
        ax.plot([0, 1], [0, 1], "k--", lw=1, label="chance")
        ax.set_title("%s\nCatBoost one-vs-rest  |  macro AUC %.3f  (tree: %.3f)" % (
            title, summary["catboost"][s]["macro_auc"], summary["tree_depth3"][s]["macro_auc"]))
        ax.set_xlabel("False positive rate")
        ax.set_ylabel("True positive rate")
        ax.legend(loc="lower right")
        ax.grid(alpha=.3)
    fig.suptitle("Modality classifier - ROC (n_seen=%d, n_unseen=%d)" % (len(d["test_seen"]["y"]), len(d["test_unseen"]["y"])), y=1.02)
    fig.tight_layout()
    fig.savefig(outdir / "roc_curves.png", dpi=130, bbox_inches="tight")
    plt.close(fig)

    # ---- figure 2: diagnostics
    fig, axes = plt.subplots(2, 2, figsize=(12, 9.5))
    ax = axes[0][0]
    pred = np.array([CLASSES[i] for i in P["catboost"]["test_unseen"].argmax(axis=1)])
    cm = confusion_matrix(d["test_unseen"]["y"], pred, labels=CLASSES)
    ax.imshow(cm, cmap="Blues")
    ax.set_xticks(range(4)); ax.set_xticklabels(CLASSES, rotation=20); ax.set_yticks(range(4)); ax.set_yticklabels(CLASSES)
    for i in range(4):
        for j in range(4):
            ax.text(j, i, cm[i, j], ha="center", va="center", color="white" if cm[i, j] > cm.max() / 2 else "black")
    ax.set_xlabel("predicted"); ax.set_ylabel("true"); ax.set_title("Confusion matrix - UNSEEN templates (CatBoost)")

    ax = axes[0][1]
    taus = np.linspace(0.25, 0.99, 60)
    for s, col in (("test_seen", "#1b9e77"), ("test_unseen", "#d95f02")):
        pr = P["catboost"][s]
        yp = np.array([CLASSES[i] for i in pr.argmax(axis=1)])
        cov, acc = [], []
        for t in taus:
            keep = pr.max(axis=1) >= t
            cov.append(keep.mean())
            acc.append((yp[keep] == d[s]["y"][keep]).mean() if keep.any() else np.nan)
        ax.plot(cov, acc, color=col, lw=2, label=s)
    ax.set_xlabel("Coverage (fraction answered)"); ax.set_ylabel("Accuracy on answered")
    ax.set_title("Selective prediction: abstaining does not rescue UNSEEN"); ax.set_ylim(0, 1.02); ax.grid(alpha=.3); ax.legend()

    ax = axes[1][0]
    for s, col, lab in (("test_seen", "#1b9e77", "seen (should answer)"), ("test_unseen", "#d95f02", "unseen"), ("ood", "#7570b3", "OOD (should abstain)")):
        ax.plot(taus, [(P["catboost"][s].max(axis=1) < t).mean() for t in taus], color=col, lw=2, label=lab)
    ax.axvline(0.6, color="k", ls=":", lw=1); ax.text(0.61, 0.05, "tau=0.6 (pre-registered)", fontsize=9)
    ax.set_xlabel("Confidence threshold tau"); ax.set_ylabel("Abstain rate")
    ax.set_title("Abstain rate vs threshold (CatBoost)"); ax.grid(alpha=.3); ax.legend()

    ax = axes[1][1]
    imp = sorted(zip(d["train"]["names"], cb.get_feature_importance()), key=lambda x: x[1])[-10:]
    ax.barh([n for n, _ in imp], [v for _, v in imp], color="#666")
    ax.set_title("CatBoost top-10 features\n(color_entropy etc. = generator texture, not modality)"); ax.set_xlabel("importance")
    fig.tight_layout()
    fig.savefig(outdir / "diagnostics.png", dpi=130, bbox_inches="tight")
    plt.close(fig)
    (outdir / "auc_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
