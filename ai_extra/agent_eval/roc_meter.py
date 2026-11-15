"""ROC / AUC and diagnostics for agent/behavior.py PersonaMeter on constructed sessions.

Observations are CONSTRUCTED here, so this checks construct validity (does the meter separate injected defects,
react to the wrong cause, or raise false alarms on harmless noise?), not prediction of real users.
Run with the project's agent package importable:  PYTHONPATH=<repo> python roc_meter.py <out_dir>
Exit code: 0 pass, 1 fail (pre-registered criteria in README.md).
"""
import json
import random
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from sklearn.metrics import roc_auc_score, roc_curve  # noqa: E402

from agent.behavior import CAUSES, PersonaMeter  # noqa: E402
from agent.schemas import Persona  # noqa: E402

CAUSE_KEYS = list(CAUSES)
N_PER_CONDITION = 300
SEED = 20261115


def field(label, typ="text"):
    return {"tag": "input", "type": typ, "label": label, "text": "", "box": {"width": 240, "height": 36}}


def build(defect, severity, rng, limit):
    """Return a list of observe() kwargs for one session."""
    strong = severity == "strong"
    detail_text = "가정식 한 상 12,000원 (배송비 3,000원 별도)"
    cart_text = "합계 15,000원"
    signup_required = rng.randint(1, max(1, limit))          # clean: never above the persona's limit
    signup_elements = [field("이메일"), field("비밀번호", "password")]
    cart_wait, extra_text = None, ""
    if defect == "input_burden":
        signup_required = limit + (5 if strong else 1)
    elif defect == "cost":
        detail_text = "가정식 한 상 12,000원"
        cart_text = "합계 15,000원 배송비 3,500원이 추가됩니다."
    elif defect == "value":
        detail_text = "가정식 한 상"
    elif defect == "trust":
        signup_elements.append(field("휴대폰 번호", "tel"))
    elif defect == "accessibility":
        signup_elements += [field("", "text") for _ in range(4 if strong else 2)]
    elif defect == "technical":
        cart_wait = 6.0 if strong else 3.6
        extra_text = " Internal Server Error" if strong else ""

    def obs(stage, required=0, text="", elements=(), last_action=None, wait=None):
        w = wait if wait is not None else min(2.0, max(0.1, rng.gauss(0.8, 0.4)))
        if wait is None and rng.random() < 0.02:               # harmless noise: an occasional slow response
            w = 3.5
        return dict(stage=stage, required_fields=required, text=text, elements=list(elements), last_action=last_action,
                    wait_seconds=w, viewport_width=1000)

    seq = [obs("landing", text="반찬 구독 시작하기"), obs("browse", text="이번 주 식탁")]
    noise_scrolls = 0 if defect != "none" else rng.choice([0, 0, 1, 1, 2, 3])   # harmless noise: ordinary scrolling
    if defect == "none":
        seq += [obs("browse", text="이번 주 식탁", last_action="scroll") for _ in range(noise_scrolls)]
    seq.append(obs("detail", text=detail_text))
    if defect == "navigation":
        wanders = 8 if strong else 3
        seq += [obs("detail", text=detail_text, last_action=rng.choice(["scroll", "back", "wait", "reload"])) for _ in range(wanders)]
        seq += [obs("browse", text="이번 주 식탁", last_action="back") for _ in range(2 if strong else 1)]
        seq.append(obs("detail", text=detail_text, last_action="click"))
    seq.append(obs("cart", text=cart_text + extra_text, wait=cart_wait))
    seq.append(obs("signup", required=signup_required, elements=signup_elements, text="회원가입"))
    seq.append(obs("payment", elements=[field("받는 분"), field("주소")], text="결제 정보"))
    seq.append(obs("complete", text="주문이 완료되었습니다"))
    return seq


def run_session(defect, severity, rng):
    limit = rng.choice([2, 4, 6])
    clip = lambda v: int(min(95, max(5, v)))  # noqa: E731
    persona = Persona(id="p%d" % rng.randrange(10 ** 6), max_required_fields=limit,
                      patience=clip(rng.gauss(50, 20)), digital_literacy=clip(rng.gauss(50, 20)),
                      price_sensitivity=clip(rng.gauss(50, 20)), trust_need=clip(rng.gauss(50, 20)))
    meter = PersonaMeter(persona, seed=rng.randrange(1, 10 ** 6))
    best, crossed_at, primary = 0.0, None, None
    for i, o in enumerate(build(defect, severity, rng, limit)):
        r = meter.observe(**o)
        best = max(best, r["score"])
        if crossed_at is None and r["crossed"]:
            crossed_at, primary = i, meter.primary_cause()
    return {"defect": defect, "severity": severity, "ratio": best / meter.threshold, "crossed": crossed_at is not None,
            "primary": primary, "contrib": dict(meter.contributions), "patience": meter.variables["patience"]}


def dose_response(rng):
    """Dropout rate vs how far the required fields exceed the persona's limit, for low/high patience."""
    out = {"low_patience": {}, "high_patience": {}}
    for k in range(0, 9):
        for name, (lo, hi) in (("low_patience", (5, 35)), ("high_patience", (65, 95))):
            hits = 0
            for _ in range(300):
                limit = rng.choice([2, 4, 6])
                persona = Persona(id="d%d" % rng.randrange(10 ** 6), max_required_fields=limit, patience=rng.randint(lo, hi))
                meter = PersonaMeter(persona, seed=rng.randrange(1, 10 ** 6))
                seq = build("none", "mild", rng, limit)
                for o in seq:
                    if o["stage"] == "signup":
                        o["required_fields"] = limit + k
                    r = meter.observe(**o)
                    if r["crossed"]:
                        hits += 1
                        break
            out[name][k] = hits / 300
    return out


def main():
    outdir = Path(sys.argv[1])
    outdir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(SEED)
    sessions = [run_session("none", "none", rng) for _ in range(N_PER_CONDITION * 2)]
    for defect in CAUSE_KEYS:
        for severity in ("mild", "strong"):
            sessions += [run_session(defect, severity, rng) for _ in range(N_PER_CONDITION)]
    defect = np.array([s["defect"] != "none" for s in sessions])
    ratio = np.array([s["ratio"] for s in sessions])
    sev = np.array([s["severity"] for s in sessions])
    res = {"n_sessions": len(sessions), "seed": SEED, "n_per_condition": N_PER_CONDITION}

    def auc_where(mask):
        return float(roc_auc_score(defect[mask], ratio[mask]))

    res["dropout_auc"] = {"all": round(auc_where(np.ones(len(sessions), bool)), 4),
                          "mild": round(auc_where((sev == "mild") | ~defect), 4),
                          "strong": round(auc_where((sev == "strong") | ~defect), 4)}
    res["false_alarm_rate_clean"] = round(float(np.mean([s["crossed"] for s in sessions if s["defect"] == "none"])), 4)
    res["detection_rate"] = {d: {sv: round(float(np.mean([s["crossed"] for s in sessions if s["defect"] == d and s["severity"] == sv])), 3)
                                 for sv in ("mild", "strong")} for d in CAUSE_KEYS}
    res["dropout_auc_by_cause"] = {d: round(float(roc_auc_score(
        [s["defect"] == d for s in sessions if s["defect"] in (d, "none")],
        [s["ratio"] for s in sessions if s["defect"] in (d, "none")])), 4) for d in CAUSE_KEYS}
    # per-cause detector (contribution score) one-vs-rest
    det_auc, curves = {}, {}
    for k in CAUSE_KEYS:
        y = [s["defect"] == k for s in sessions]
        score = [s["contrib"][k] for s in sessions]
        det_auc[k] = round(float(roc_auc_score(y, score)), 4)
        curves[k] = roc_curve(y, score)[:2]
    res["detector_auc"] = det_auc
    # cross-talk: mean contribution of detector k under injected cause c, relative to detector c's own mean
    mean = {c: {k: float(np.mean([s["contrib"][k] for s in sessions if s["defect"] == c])) for k in CAUSE_KEYS} for c in CAUSE_KEYS}
    cross = {}
    for c in CAUSE_KEYS:
        own = mean[c][c] or 1e-9
        for k in CAUSE_KEYS:
            if k != c and mean[c][k] / own >= 0.30:
                cross["%s <- injected %s" % (k, c)] = round(mean[c][k] / own, 3)
    res["cross_talk_ge_30pct_of_own"] = cross
    # primary cause attribution among dropped-out defect sessions
    cm = np.zeros((len(CAUSE_KEYS), len(CAUSE_KEYS)))
    dropped = [s for s in sessions if s["defect"] != "none" and s["crossed"]]
    for s in dropped:
        if s["primary"] in CAUSE_KEYS:
            cm[CAUSE_KEYS.index(s["defect"]), CAUSE_KEYS.index(s["primary"])] += 1
    res["primary_cause_accuracy"] = round(float(np.trace(cm) / cm.sum()), 4) if cm.sum() else None
    res["primary_cause_n"] = int(cm.sum())
    res["primary_cause_recall_by_injected"] = {c: (round(float(cm[i, i] / cm[i].sum()), 3) if cm[i].sum() else None) for i, c in enumerate(CAUSE_KEYS)}
    res["dose_response_input_burden"] = dose_response(random.Random(SEED + 1))
    allowed_cross = []  # no designed co-firing pairs are expected from the constructed sessions
    checks = {
        "dropout_auc_all": (res["dropout_auc"]["all"], ">=", 0.85),
        "dropout_auc_mild": (res["dropout_auc"]["mild"], ">=", 0.70),
        "detector_auc_min": (min(det_auc.values()), ">=", 0.90),
        "false_alarm_rate_clean": (res["false_alarm_rate_clean"], "<=", 0.10),
        "primary_cause_accuracy": (res["primary_cause_accuracy"], ">=", 0.80),
        "unexpected_cross_talk_pairs": (len([k for k in cross if k not in allowed_cross]), "==", 0),
    }
    res["checks"] = {k: {"value": v, "op": op, "threshold": t, "pass": bool({">=": v >= t, "<=": v <= t, "==": v == t}[op])} for k, (v, op, t) in checks.items()}
    res["verdict"] = "pass" if all(c["pass"] for c in res["checks"].values()) else "fail"

    # ---- plots
    fig, axes = plt.subplots(1, 3, figsize=(17, 5.2))
    ax = axes[0]
    for name, mask, col in (("all defects", np.ones(len(sessions), bool), "#333"), ("mild only", (sev == "mild") | ~defect, "#d95f02"),
                            ("strong only", (sev == "strong") | ~defect, "#1b9e77")):
        fpr, tpr, _ = roc_curve(defect[mask], ratio[mask])
        ax.plot(fpr, tpr, lw=2, color=col, label="%s  AUC=%.3f" % (name, auc_where(mask)))
    fa = res["false_alarm_rate_clean"]
    ax.scatter([fa], [np.mean([s["crossed"] for s in sessions if s["defect"] != "none"])], color="red", zorder=5, label="agent's actual decision (ratio>=1)")
    ax.plot([0, 1], [0, 1], "k--", lw=1)
    ax.set(title="Dropout decision: defect vs clean\n(score/threshold, 4500 constructed sessions)", xlabel="False positive rate", ylabel="True positive rate")
    ax.legend(loc="lower right", fontsize=8); ax.grid(alpha=.3)
    ax = axes[1]
    for k in CAUSE_KEYS:
        ax.plot(*curves[k], lw=1.8, label="%s AUC=%.3f" % (k, det_auc[k]))
    ax.plot([0, 1], [0, 1], "k--", lw=1)
    ax.set(title="Per-cause detector ROC (one-vs-rest)\ncontribution score of cause k", xlabel="False positive rate", ylabel="True positive rate")
    ax.legend(loc="lower right", fontsize=8); ax.grid(alpha=.3)
    ax = axes[2]
    ax.imshow(cm / np.maximum(cm.sum(axis=1, keepdims=True), 1), cmap="Blues", vmin=0, vmax=1)
    ax.set_xticks(range(7)); ax.set_xticklabels(CAUSE_KEYS, rotation=40, ha="right", fontsize=8)
    ax.set_yticks(range(7)); ax.set_yticklabels(CAUSE_KEYS, fontsize=8)
    for i in range(7):
        for j in range(7):
            if cm[i, j]:
                ax.text(j, i, int(cm[i, j]), ha="center", va="center", fontsize=8, color="white" if cm[i, j] / max(cm[i].sum(), 1) > .5 else "black")
    ax.set(title="Primary cause named at dropout (row-normalised)\naccuracy %s, n=%d" % (res["primary_cause_accuracy"], res["primary_cause_n"]),
           xlabel="agent's primary cause", ylabel="injected cause")
    fig.tight_layout(); fig.savefig(outdir / "meter_roc.png", dpi=130, bbox_inches="tight"); plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(13, 4.8))
    ax = axes[0]
    ks = sorted(res["dose_response_input_burden"]["low_patience"])
    for name, col in (("low_patience", "#d95f02"), ("high_patience", "#1b9e77")):
        ax.plot(ks, [res["dose_response_input_burden"][name][k] for k in ks], marker="o", color=col, label=name.replace("_", " "))
    ax.set(title="Dose-response: dropout rate vs required fields above the limit\n(input_burden, harmless noise on)", xlabel="fields above persona limit", ylabel="dropout rate", ylim=(-.02, 1.02))
    ax.legend(); ax.grid(alpha=.3)
    ax = axes[1]
    rates = np.array([[res["detection_rate"][d]["mild"], res["detection_rate"][d]["strong"]] for d in CAUSE_KEYS])
    xs = np.arange(len(CAUSE_KEYS))
    ax.bar(xs - .2, rates[:, 0], .4, label="mild", color="#d95f02"); ax.bar(xs + .2, rates[:, 1], .4, label="strong", color="#1b9e77")
    ax.axhline(fa, color="red", ls="--", lw=1, label="clean false-alarm %.2f" % fa)
    ax.set_xticks(xs); ax.set_xticklabels(CAUSE_KEYS, rotation=30, ha="right")
    ax.set(title="Actual dropout rate by injected cause and severity", ylabel="fraction crossing threshold", ylim=(0, 1.05)); ax.legend(); ax.grid(alpha=.3, axis="y")
    fig.tight_layout(); fig.savefig(outdir / "meter_dose_detection.png", dpi=130, bbox_inches="tight"); plt.close(fig)

    (outdir / "run1.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print("verdict:", res["verdict"])
    for k, c in res["checks"].items():
        print("  %-30s %s %s %s -> %s" % (k, c["value"], c["op"], c["threshold"], "pass" if c["pass"] else "FAIL"))
    print("detector AUC:", det_auc)
    print("detection rate (mild/strong):", {d: tuple(v.values()) for d, v in res["detection_rate"].items()})
    print("cross-talk >=30%:", cross)
    print("primary recall:", res["primary_cause_recall_by_injected"])
    sys.exit(0 if res["verdict"] == "pass" else 1)


if __name__ == "__main__":
    main()
