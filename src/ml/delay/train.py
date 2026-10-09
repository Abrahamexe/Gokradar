"""Gecikme modeli eğitimi: Logistic Regression baseline + LightGBM.

Veri: yerel Parquet'ler — bts_flights (load_bts), weather_hourly ve weather_dest_hourly
(load_weather). Neon'a gidilmez: geçmiş uçuşlar yalnızca eğitimde gerekir ve 12+ aylık veri
ücretsiz katmanın sınırını zorlar.
Bölme tarih bazlıdır:
    eğitim  : ilk günler
    doğrulama: testten önceki DELAY_VAL_DAYS gün (erken durdurma + eşik seçimi)
    test    : son DELAY_TEST_DAYS gün (yalnızca raporlama)

Çalıştırma (proje kökünden):  python -m src.ml.delay.train
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import joblib  # noqa: E402
import lightgbm as lgb  # noqa: E402
import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from sklearn.compose import ColumnTransformer  # noqa: E402
from sklearn.impute import SimpleImputer  # noqa: E402
from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.pipeline import Pipeline, make_pipeline  # noqa: E402
from sklearn.preprocessing import OneHotEncoder, StandardScaler  # noqa: E402

import config  # noqa: E402
from src.features.delay_features import (CATEGORICAL, FEATURES, NUMERIC, ORDINAL,  # noqa: E402
                                         DelayFeatureBuilder, attach_weather)
from src.ml.delay.evaluate import (best_f1_threshold, compute_metrics, format_table,  # noqa: E402
                                   save_calibration_plot)
from src.ingest.load_bts import OUTPUT_PATH as FLIGHTS_PATH  # noqa: E402
from src.ingest.load_weather import DEST_OUTPUT_PATH as DEST_WEATHER_PATH  # noqa: E402
from src.ingest.load_weather import OUTPUT_PATH as WEATHER_PATH  # noqa: E402

TARGET = "dep_del15"
MODEL_PATH = config.DELAY_MODEL_DIR / "delay_model.joblib"
METRICS_PATH = config.DELAY_MODEL_DIR / "metrics.json"
REPORT_PATH = config.DELAY_MODEL_DIR / "report.md"
IMPORTANCE_PATH = config.DELAY_MODEL_DIR / "feature_importance.png"
CALIBRATION_PATH = config.DELAY_MODEL_DIR / "calibration.png"

RECENCY_HALF_LIFE_DAYS = 90
LGBM_PARAMS = dict(
    n_estimators=5000, learning_rate=0.03, num_leaves=63, min_child_samples=200,
    subsample=0.8, subsample_freq=1, colsample_bytree=0.8, reg_lambda=1.0,
    # Çok değerli kategorikler (varış, havayolu) için düzenlileştirme — doğrulama setinde seçildi
    cat_smooth=50, min_data_per_group=500, cat_l2=20,
    random_state=42, n_jobs=-1, verbose=-1,
)


def load_data() -> pd.DataFrame:
    for path, cmd in [(FLIGHTS_PATH, "load_bts"), (WEATHER_PATH, "load_weather"),
                      (DEST_WEATHER_PATH, "load_weather --dest")]:
        if not path.exists():
            sys.exit(f"{path.name} yok; önce `python -m src.ingest.{cmd}` çalıştırın.")
    flights = pd.read_parquet(FLIGHTS_PATH)
    # İptal edilen uçuşlar hedef tanımına girmez (kalkış gecikmesi yok)
    flights = flights[~flights["cancelled"] & flights["dep_del15"].notna()].drop(columns="cancelled")
    flights["dep_del15"] = flights["dep_del15"].astype(bool)
    weather = pd.read_parquet(WEATHER_PATH)
    dest_weather = pd.read_parquet(DEST_WEATHER_PATH)
    df = attach_weather(flights, weather, dest_weather)
    print(f"Veri: {len(df):,} uçuş | METAR eşleşme oranı: kalkış {df['temp_c'].notna().mean():.2%}, "
          f"varış {df['dest_wind_kt'].notna().mean():.2%}")
    return df


def time_split(df: pd.DataFrame):
    dates = pd.to_datetime(df["flight_date"])
    test_start = dates.max().normalize() - pd.Timedelta(days=config.DELAY_TEST_DAYS - 1)
    val_start = test_start - pd.Timedelta(days=config.DELAY_VAL_DAYS)
    train = df[dates < val_start]
    val = df[(dates >= val_start) & (dates < test_start)]
    test = df[dates >= test_start]
    for name, part in [("Eğitim", train), ("Doğrulama", val), ("Test", test)]:
        d = pd.to_datetime(part["flight_date"])
        print(f"  {name:10s}: {d.min():%Y-%m-%d} → {d.max():%Y-%m-%d} | {len(part):>7,} uçuş | "
              f"gecikmeli oranı {part[TARGET].mean():.2%}")
    return train, val, test


def make_logreg(class_weight=None) -> Pipeline:
    pre = ColumnTransformer([
        ("cat", OneHotEncoder(handle_unknown="ignore", min_frequency=20), CATEGORICAL + ORDINAL),
        ("num", make_pipeline(SimpleImputer(strategy="median"), StandardScaler()), NUMERIC),
    ])
    return Pipeline([("pre", pre),
                     ("clf", LogisticRegression(max_iter=2000, C=1.0, class_weight=class_weight))])


def recency_weights(train: pd.DataFrame) -> np.ndarray:
    """Zaman sönümlü örnek ağırlığı: eğitimin son gününden RECENCY_HALF_LIFE_DAYS gün önceki
    uçuş yarı ağırlık alır. Gecikme düzeyi aydan aya kaydığı için yakın geçmiş daha önemli;
    eski aylar yine de mevsim bilgisini taşır (Ocak/Mart/Temmuz testlerinde seçildi)."""
    dates = pd.to_datetime(train["flight_date"])
    age_days = (dates.max() - dates).dt.days.to_numpy()
    return 0.5 ** (age_days / RECENCY_HALF_LIFE_DAYS)


def fit_lgbm(X_train, y_train, X_val, y_val, class_weight=None,
             sample_weight=None) -> lgb.LGBMClassifier:
    model = lgb.LGBMClassifier(**LGBM_PARAMS, class_weight=class_weight)
    model.fit(X_train, y_train, sample_weight=sample_weight, eval_X=X_val, eval_y=y_val,
              eval_metric="auc",
              callbacks=[lgb.early_stopping(200, verbose=False)])
    print(f"    en iyi iterasyon: {model.best_iteration_}")
    return model


def save_importance_plot(model: lgb.LGBMClassifier) -> pd.Series:
    gain = pd.Series(model.booster_.feature_importance(importance_type="gain"),
                     index=model.booster_.feature_name()).sort_values()
    gain = gain / gain.sum()
    fig, ax = plt.subplots(figsize=(8, 6))
    ax.barh(gain.index, gain.values, color="#3b6ea5")
    ax.set_xlabel("Toplam kazanç payı (gain)")
    ax.set_title("LightGBM özellik önemi — gecikme modeli")
    for i, v in enumerate(gain.values):
        ax.text(v, i, f" {v:.1%}", va="center", fontsize=8)
    fig.tight_layout()
    fig.savefig(IMPORTANCE_PATH, dpi=120)
    plt.close(fig)
    return gain.sort_values(ascending=False)


def main() -> None:
    df = load_data()
    print("Tarih bazlı bölme:")
    train, val, test = time_split(df)

    builder = DelayFeatureBuilder().fit(train)
    X_train, X_val, X_test = (builder.transform(p) for p in (train, val, test))
    y_train, y_val, y_test = (p[TARGET].astype(int).to_numpy() for p in (train, val, test))
    n_other = (X_test["dest"] == "OTHER").mean()
    print(f"Varış havalimanı: {len(builder.dests_) - 1} ayrı + OTHER "
          f"(testte OTHER payı {n_other:.2%})")

    results, models = {}, {}

    def evaluate(name, proba_val, proba_test, fixed_threshold=None):
        threshold = fixed_threshold if fixed_threshold is not None else best_f1_threshold(y_val, proba_val)
        results[name] = compute_metrics(y_test, proba_test, threshold)

    print("\nLogistic Regression (baseline)...")
    lr = make_logreg().fit(X_train, y_train)
    evaluate("LogReg (eşik ayarlı)", lr.predict_proba(X_val)[:, 1], lr.predict_proba(X_test)[:, 1])
    models["logreg"] = lr

    print("LightGBM (class_weight='balanced')...")
    w_train = recency_weights(train)
    lgbm_bal = fit_lgbm(X_train, y_train, X_val, y_val, class_weight="balanced", sample_weight=w_train)
    evaluate("LightGBM (class weight, eşik 0.5)",
             lgbm_bal.predict_proba(X_val)[:, 1], lgbm_bal.predict_proba(X_test)[:, 1], 0.5)

    print("LightGBM (ağırlıksız)...")
    lgbm = fit_lgbm(X_train, y_train, X_val, y_val, sample_weight=w_train)
    p_val, p_test = lgbm.predict_proba(X_val)[:, 1], lgbm.predict_proba(X_test)[:, 1]
    evaluate("LightGBM (eşik 0.5)", p_val, p_test, 0.5)
    evaluate("LightGBM (eşik ayarlı) ★", p_val, p_test)
    threshold = results["LightGBM (eşik ayarlı) ★"]["threshold"]

    test_rate = y_test.mean()
    table = format_table(results)
    importance = save_importance_plot(lgbm)
    eces = save_calibration_plot(y_test, {"LogReg": lr.predict_proba(X_test)[:, 1],
                                          "LightGBM": p_test}, CALIBRATION_PATH)

    print(f"\nTest seti: {len(y_test):,} uçuş | gecikmeli (≥15 dk) oranı: {test_rate:.2%} "
          f"(rastgele modelin PR-AUC'si ≈ {test_rate:.4f})\n")
    print(table)
    print("\nÖzellik önemi (gain payı):")
    print(importance.map("{:.1%}".format).to_string())
    print("\nKalibrasyon (ECE, test):", ", ".join(f"{k}={v:.4f}" for k, v in eces.items()))

    # --- kaydet ---
    config.DELAY_MODEL_DIR.mkdir(parents=True, exist_ok=True)
    version = datetime.now(timezone.utc).strftime("lgbm-%Y%m%d-%H%M%S")
    joblib.dump({
        "version": version,
        "builder": builder,
        "model": lgbm,
        "baseline": lr,
        "threshold": threshold,
        "features": FEATURES,
        "train_period": [str(train["flight_date"].min())[:10], str(train["flight_date"].max())[:10]],
    }, MODEL_PATH)
    METRICS_PATH.write_text(json.dumps({
        "version": version, "test_delay_rate": test_rate, "n_test": int(len(y_test)),
        "n_train": int(len(y_train)),
        "best_iteration": lgbm.best_iteration_, "results": results,
        "feature_importance_gain": importance.to_dict(), "calibration_ece": eces,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    REPORT_PATH.write_text(
        f"# Gecikme modeli — {version}\n\nTest: {len(y_test):,} uçuş, gecikmeli oranı "
        f"{test_rate:.2%}\n\n{table}\n", encoding="utf-8")
    print(f"\nKaydedildi: {MODEL_PATH.relative_to(config.BASE_DIR)}, metrics.json, report.md, "
          f"{IMPORTANCE_PATH.name}, {CALIBRATION_PATH.name}")


if __name__ == "__main__":
    main()
