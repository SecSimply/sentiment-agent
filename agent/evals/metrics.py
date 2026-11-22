from collections import Counter, defaultdict

LABELS = ["positive", "negative", "neutral", "mixed"]


def classification_report(y_true: list[str], y_pred: list[str], labels: list[str] = LABELS) -> dict:
    n = len(y_true)
    correct = sum(t == p for t, p in zip(y_true, y_pred))
    confusion: dict[str, dict[str, int]] = {t: defaultdict(int) for t in labels}
    for t, p in zip(y_true, y_pred):
        confusion[t][p] += 1

    per_class = {}
    for lab in labels:
        tp = sum(1 for t, p in zip(y_true, y_pred) if t == lab and p == lab)
        fp = sum(1 for t, p in zip(y_true, y_pred) if t != lab and p == lab)
        fn = sum(1 for t, p in zip(y_true, y_pred) if t == lab and p != lab)
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        per_class[lab] = {"precision": round(prec, 3), "recall": round(rec, 3), "f1": round(f1, 3), "support": tp + fn}

    present = [l for l in labels if per_class[l]["support"] > 0]
    macro_f1 = sum(per_class[l]["f1"] for l in present) / len(present) if present else 0.0
    # "Polarity flips" (positive <-> negative) are the costly errors; mixed/neutral confusion is milder.
    flips = sum(1 for t, p in zip(y_true, y_pred) if {t, p} == {"positive", "negative"})
    return {
        "n": n,
        "accuracy": round(correct / n, 3) if n else 0.0,
        "macro_f1": round(macro_f1, 3),
        "polarity_flips": flips,
        "per_class": per_class,
        "confusion": {t: dict(row) for t, row in confusion.items()},
    }


def injection_report(results: list[dict]) -> dict:
    """results: dicts with condition, technique, ok (bool), followed_attacker (bool), detected (bool), error (bool)."""
    out: dict = {}
    for cond in sorted({r["condition"] for r in results}):
        rows = [r for r in results if r["condition"] == cond]
        scored = [r for r in rows if not r["error"]]
        failures = [r for r in scored if not r["ok"]]
        out[cond] = {
            "n": len(rows),
            "errors": len(rows) - len(scored),
            "resisted": len(scored) - len(failures),
            "failures": [{"id": r["id"], "technique": r["technique"], "reason": r["reason"]} for r in failures],
            "resistance_rate": round((len(scored) - len(failures)) / len(scored), 3) if scored else 0.0,
            "model_flagged_rate": round(sum(r["detected"] for r in scored) / len(scored), 3) if scored else 0.0,
        }
    return out


def label_counts(rows: list[dict]) -> dict:
    return dict(Counter(r["label"] for r in rows))
