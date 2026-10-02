"""Gecikme modeli değerlendirme metrikleri, eşik seçimi ve kalibrasyon grafiği."""
from pathlib import Path

import numpy as np
from sklearn.calibration import calibration_curve
from sklearn.metrics import (average_precision_score, brier_score_loss, confusion_matrix,
                             f1_score, precision_recall_curve, precision_score, recall_score,
                             roc_auc_score)


def best_f1_threshold(y_true, proba) -> float:
    """F1'i en yükseğe çıkaran olasılık eşiği (doğrulama setinde seçilir, testte değil)."""
    precision, recall, thresholds = precision_recall_curve(y_true, proba)
    f1 = 2 * precision * recall / np.clip(precision + recall, 1e-12, None)
    return float(thresholds[np.argmax(f1[:-1])])


def compute_metrics(y_true, proba, threshold: float) -> dict:
    y_pred = (proba >= threshold).astype(int)
    tn, fp, fn, tp = confusion_matrix(y_true, y_pred, labels=[0, 1]).ravel()
    return {
        "roc_auc": roc_auc_score(y_true, proba),
        "pr_auc": average_precision_score(y_true, proba),
        "f1": f1_score(y_true, y_pred),
        "precision": precision_score(y_true, y_pred, zero_division=0),
        "recall": recall_score(y_true, y_pred),
        "brier": brier_score_loss(y_true, proba),
        "threshold": threshold,
        "tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp),
    }


def expected_calibration_error(y_true, proba, n_bins: int = 10) -> float:
    """Eşit sayılı kutularda |ortalama tahmin − gerçekleşen oran| ağırlıklı ortalaması."""
    y_true, proba = np.asarray(y_true), np.asarray(proba)
    edges = np.quantile(proba, np.linspace(0, 1, n_bins + 1))
    idx = np.clip(np.searchsorted(edges, proba, side="right") - 1, 0, n_bins - 1)
    ece = 0.0
    for b in range(n_bins):
        mask = idx == b
        if mask.any():
            ece += mask.mean() * abs(proba[mask].mean() - y_true[mask].mean())
    return float(ece)


def save_calibration_plot(y_true, probas: dict[str, np.ndarray], path: Path,
                          n_bins: int = 10) -> dict[str, float]:
    """Güvenilirlik diyagramı (eşit sayılı kutular) + tahmin dağılımı. Model → ECE döner."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (ax, ax_hist) = plt.subplots(2, 1, figsize=(7, 8), height_ratios=[3, 1], sharex=True)
    ax.plot([0, 1], [0, 1], ls="--", color="grey", lw=1, label="Mükemmel kalibrasyon")
    eces = {}
    for name, proba in probas.items():
        frac_pos, mean_pred = calibration_curve(y_true, proba, n_bins=n_bins, strategy="quantile")
        eces[name] = expected_calibration_error(y_true, proba, n_bins)
        line, = ax.plot(mean_pred, frac_pos, marker="o",
                        label=f"{name} (ECE={eces[name]:.3f}, Brier={brier_score_loss(y_true, proba):.4f})")
        ax_hist.hist(proba, bins=40, range=(0, 1), histtype="step", color=line.get_color(), lw=1.5)
    ax.axhline(np.mean(y_true), color="black", lw=0.8, alpha=0.4)
    ax.set_ylabel("Gerçekleşen gecikme oranı")
    ax.set_title("Kalibrasyon — test seti (her nokta ≈ %10'luk dilim)")
    ax.legend(loc="upper left", fontsize=8)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax_hist.set_xlabel("Tahmin edilen gecikme olasılığı")
    ax_hist.set_ylabel("Uçuş sayısı")
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return eces


def format_table(results: dict[str, dict]) -> str:
    """Model adı → metrikler sözlüğünü Markdown tablosuna çevirir."""
    header = ("| Model | ROC-AUC | PR-AUC | F1 | Precision | Recall | Eşik | Brier "
              "| TN | FP | FN | TP |")
    lines = [header, "|" + "---|" * 12]
    for name, m in results.items():
        lines.append(
            f"| {name} | {m['roc_auc']:.4f} | {m['pr_auc']:.4f} | {m['f1']:.4f} | "
            f"{m['precision']:.4f} | {m['recall']:.4f} | {m['threshold']:.3f} | {m['brier']:.4f} | "
            f"{m['tn']:,} | {m['fp']:,} | {m['fn']:,} | {m['tp']:,} |")
    return "\n".join(lines)
