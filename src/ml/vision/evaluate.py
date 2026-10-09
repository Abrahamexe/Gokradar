"""Son modelin test seti değerlendirmesi — yalnızca en sonda, bir kez çalıştırılır.

Aynı model sürümü için sonuç varsa tekrar çalışmaz (bilerek yeniden ölçmek için --force).

Çalıştırma (proje kökünden):  python -m src.ml.vision.evaluate
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import torch  # noqa: E402

from src.ml.vision.common import MODEL_DIR, MODEL_PATH, aircraft_dataset, build_model  # noqa: E402
from src.ml.vision.train import DEVICE, loader, metrics, predict_logits  # noqa: E402

METRICS_PATH = MODEL_DIR / "test_metrics.json"
PER_CLASS_PATH = MODEL_DIR / "test_per_class.csv"
CONFUSION_CSV = MODEL_DIR / "test_confusion_matrix.csv"
CONFUSION_PNG = MODEL_DIR / "test_confusion_matrix.png"
REPORT_PATH = MODEL_DIR / "report.md"


def top_confusions(cm: np.ndarray, classes: list[str], n: int = 10) -> pd.DataFrame:
    rows = [(classes[i], classes[j], int(cm[i, j]), cm[i, j] / cm[i].sum())
            for i in range(len(classes)) for j in range(len(classes)) if i != j and cm[i, j] > 0]
    df = pd.DataFrame(rows, columns=["gercek", "tahmin", "adet", "gercek_sinif_payi"])
    return df.sort_values(["adet", "gercek_sinif_payi"], ascending=False).head(n).reset_index(drop=True)


def save_confusion_png(cm: np.ndarray, classes: list[str]) -> None:
    norm = cm / cm.sum(axis=1, keepdims=True)
    fig, ax = plt.subplots(figsize=(16, 16))
    ax.imshow(norm, cmap="Blues", vmin=0, vmax=1)
    ax.set_xticks(range(len(classes)), classes, rotation=90, fontsize=6)
    ax.set_yticks(range(len(classes)), classes, fontsize=6)
    ax.set_xlabel("Tahmin")
    ax.set_ylabel("Gerçek")
    ax.set_title("Test confusion matrix (satır normalize)")
    fig.tight_layout()
    fig.savefig(CONFUSION_PNG, dpi=110)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="test sonucu varsa bile yeniden ölç")
    args = parser.parse_args()
    ckpt = torch.load(MODEL_PATH, map_location=DEVICE, weights_only=True)
    # Kilit model sürümüne bağlı: yeni eğitilen model bir kez ölçülebilir
    if METRICS_PATH.exists() and not args.force and \
            json.loads(METRICS_PATH.read_text(encoding="utf-8")).get("version") == ckpt["version"]:
        sys.exit(f"Bu model ({ckpt['version']}) test setinde zaten değerlendirildi. "
                 f"Tekrar ölçmek için --force.")
    classes = ckpt["classes"]
    model = build_model(ckpt["arch"], len(classes), pretrained=False)
    model.load_state_dict(ckpt["state_dict"])
    model = model.to(DEVICE).to(memory_format=torch.channels_last)

    test_ds = aircraft_dataset("test", train=False)
    assert test_ds.classes == classes
    logits, targets = predict_logits(model, loader(test_ds, shuffle=False))
    m = metrics(logits, targets, len(classes))
    pred = logits.argmax(1).numpy()
    y = targets.numpy()
    fgvc = np.array(test_ds.from_fgvc)
    sources = [(name, mask) for name, mask in [("FGVC", fgvc), ("Commons", ~fgvc)] if mask.any()]
    by_source = {name: {"n": int(mask.sum()), **metrics(logits[mask], targets[mask], len(classes))}
                 for name, mask in sources}

    cm = np.zeros((len(classes), len(classes)), dtype=int)
    np.add.at(cm, (y, pred), 1)
    per_class = pd.DataFrame({"family": classes, "n": cm.sum(1),
                              "accuracy": cm.diagonal() / cm.sum(1)}).sort_values("accuracy")
    confusions = top_confusions(cm, classes)

    val = ckpt["val_selection"]
    table = ("| Metrik | Val (seçim, yalnız train ile eğitim) | Test (final, train+val) |\n"
             "|---|---|---|\n"
             f"| Top-1 doğruluk | {val['val_top1']:.2%} | {m['top1']:.2%} |\n"
             f"| Top-3 doğruluk | {val['val_top3']:.2%} | {m['top3']:.2%} |\n"
             f"| Macro doğruluk | {val['val_macro_acc']:.2%} | {m['macro_acc']:.2%} |")
    source_md = "| Kaynak | Görüntü | Top-1 | Top-3 |\n|---|---|---|---|\n" + "\n".join(
        f"| {k} | {v['n']:,} | {v['top1']:.2%} | {v['top3']:.2%} |" for k, v in by_source.items())
    conf_md = "| Gerçek | Tahmin | Adet | Gerçek sınıfın payı |\n|---|---|---|---|\n" + "\n".join(
        f"| {r.gercek} | {r.tahmin} | {r.adet} | {r.gercek_sinif_payi:.1%} |"
        for r in confusions.itertuples())
    worst_md = "| Aile | Test görüntü | Doğruluk |\n|---|---|---|\n" + "\n".join(
        f"| {r.family} | {r.n} | {r.accuracy:.1%} |" for r in per_class.head(10).itertuples())

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    per_class.to_csv(PER_CLASS_PATH, index=False)
    pd.DataFrame(cm, index=classes, columns=classes).to_csv(CONFUSION_CSV)
    save_confusion_png(cm, classes)
    REPORT_PATH.write_text(
        f"# Uçak sınıflandırıcı — {ckpt['version']}\n\nTest: {len(y):,} görüntü, "
        f"{len(classes)} aile\n\n{table}\n\n## Kaynağa göre\n\n{source_md}\n\n## En çok karıştırılan 10 çift\n\n{conf_md}\n\n"
        f"## En düşük doğruluklu 10 sınıf\n\n{worst_md}\n", encoding="utf-8")
    # "Test yapıldı" işareti en son yazılır: önceki adımlardan biri hata verirse tekrar denenebilir.
    METRICS_PATH.write_text(json.dumps({
        "version": ckpt["version"], "arch": ckpt["arch"], "n_test": len(y), **m,
        "by_source": by_source, "val_selection": val, "top_confusions": confusions.to_dict(orient="records"),
        "worst_classes": per_class.head(10).to_dict(orient="records"),
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Model: {ckpt['version']} | test: {len(y):,} görüntü\n\n{table}\n\n{source_md}\n")
    print("En düşük doğruluklu 10 sınıf:")
    print(per_class.head(10).to_string(index=False, formatters={"accuracy": "{:.1%}".format}))
    print("\nEn çok karıştırılan 10 çift:")
    print(confusions.to_string(formatters={"gercek_sinif_payi": "{:.1%}".format}))


if __name__ == "__main__":
    main()
