"""FGVC-Aircraft veri setini data/raw/fgvc/ altına indirir ve aile (family)
düzeyinde sınıf dağılımını raporlar.

Çalıştırma (proje kökünden):  python -m src.ingest.load_fgvc
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd  # noqa: E402
from torchvision.datasets import FGVCAircraft  # noqa: E402

import config  # noqa: E402

SPLITS = ["train", "val", "test"]
REPORT_PATH = config.PROCESSED_DIR / "fgvc_class_distribution.csv"


def load_split(split: str) -> FGVCAircraft:
    # İlk çağrıda arşiv (~2.7 GB) indirilir ve açılır; sonrakilerde mevcut dosyalar kullanılır.
    return FGVCAircraft(root=str(config.FGVC_DIR), split=split,
                        annotation_level="family", download=True)


def class_distribution() -> pd.DataFrame:
    counts = {}
    classes = None
    for split in SPLITS:
        ds = load_split(split)
        classes = ds.classes
        # _labels: görüntüleri açmadan etiketlere erişim (torchvision iç alanı)
        counts[split] = pd.Series(ds._labels).value_counts()
    df = pd.DataFrame(counts).reindex(range(len(classes))).fillna(0).astype(int)
    df.insert(0, "family", classes)
    df["total"] = df[SPLITS].sum(axis=1)
    return df.sort_values("total", ascending=False).reset_index(drop=True)


def main() -> None:
    df = class_distribution()
    config.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(REPORT_PATH, index=False)

    print(f"Sınıf sayısı (family): {len(df)}")
    for split in SPLITS + ["total"]:
        s = df[split]
        print(f"  {split:5s}: {s.sum():5d} görüntü | sınıf başına min={s.min()} "
              f"medyan={int(s.median())} max={s.max()}")
    print("\nEn kalabalık 5 sınıf:")
    print(df.head(5)[["family"] + SPLITS + ["total"]].to_string(index=False))
    print("\nEn az örnekli 5 sınıf:")
    print(df.tail(5)[["family"] + SPLITS + ["total"]].to_string(index=False))
    print(f"\nRapor: {REPORT_PATH.relative_to(config.BASE_DIR)}")


if __name__ == "__main__":
    main()
