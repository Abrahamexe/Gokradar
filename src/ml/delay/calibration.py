"""Kayıtlı gecikme modelinin (yeniden eğitmeden) test seti kalibrasyon grafiğini üretir
ve ECE değerlerini metrics.json'a ekler.

Çalıştırma (proje kökünden):  python -m src.ml.delay.calibration
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import joblib  # noqa: E402

import config  # noqa: E402
from src.ml.delay.evaluate import save_calibration_plot  # noqa: E402
from src.ml.delay.train import (CALIBRATION_PATH, METRICS_PATH, MODEL_PATH, TARGET,  # noqa: E402
                                load_data, time_split)


def main() -> None:
    bundle = joblib.load(MODEL_PATH)
    _, _, test = time_split(load_data())
    X_test = bundle["builder"].transform(test)
    y_test = test[TARGET].astype(int).to_numpy()
    eces = save_calibration_plot(y_test, {
        "LogReg": bundle["baseline"].predict_proba(X_test)[:, 1],
        "LightGBM": bundle["model"].predict_proba(X_test)[:, 1],
    }, CALIBRATION_PATH)

    metrics = json.loads(METRICS_PATH.read_text(encoding="utf-8"))
    if metrics.get("version") != bundle["version"]:
        sys.exit("metrics.json başka bir model sürümüne ait; güncellenmedi.")
    metrics["calibration_ece"] = eces
    METRICS_PATH.write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")
    print("ECE (test):", ", ".join(f"{k}={v:.4f}" for k, v in eces.items()))
    print(f"Kaydedildi: {CALIBRATION_PATH.relative_to(config.BASE_DIR)}")


if __name__ == "__main__":
    main()
