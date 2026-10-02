"""Gecikme tahmini: kalkış, varış, havayolu, tarih ve planlanan saatten
≥15 dk gecikme olasılığı. Hava durumu verilmezse kalkış havalimanının güncel METAR'ı çekilir.
"""
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import joblib
import pandas as pd

import config
from src.features.delay_features import WEATHER
from src.services.metar_client import get_metar

MODEL_PATH = config.DELAY_MODEL_DIR / "delay_model.joblib"


class DelayInputError(ValueError):
    pass


@dataclass
class DelayPrediction:
    probability: float
    is_delayed: bool
    threshold: float
    weather: dict | None
    model_version: str


class DelayPredictor:
    def __init__(self, path: Path = MODEL_PATH):
        if not path.exists():
            raise FileNotFoundError(f"{path} yok; önce `python -m src.ml.delay.train` çalıştırın.")
        bundle = joblib.load(path)
        self.builder = bundle["builder"]
        self.model = bundle["model"]
        self.threshold = bundle["threshold"]
        self.version = bundle["version"]
        self.train_period = bundle.get("train_period")  # ["2026-06-01", "2026-07-10"]

    @property
    def airports(self) -> list[str]:
        return list(self.builder.origins_)

    def _validate(self, origin: str, dest: str, airline: str, crs_dep_time: int) -> None:
        if origin not in self.builder.origins_:
            raise DelayInputError(f"Desteklenmeyen kalkış havalimanı: {origin}. "
                                  f"Desteklenenler: {', '.join(self.builder.origins_)}")
        if not (len(dest) == 3 and dest.isalpha()):
            raise DelayInputError(f"Geçersiz varış havalimanı kodu: {dest}")
        if not (2 <= len(airline) <= 3 and airline.isalnum()):
            raise DelayInputError(f"Geçersiz havayolu kodu: {airline}")
        if not (0 <= crs_dep_time <= 2359 and crs_dep_time % 100 < 60):
            raise DelayInputError(f"Geçersiz planlanan saat (HHMM): {crs_dep_time}")

    def predict_times(self, origin: str, dest: str, airline: str, flight_date: date,
                      crs_dep_times: list[int], weather: dict | None = None) -> list[float]:
        """Aynı uçuş için birden çok planlanan saatin gecikme olasılığı; model tek çağrıda çalışır."""
        origin, dest, airline = origin.strip().upper(), dest.strip().upper(), airline.strip().upper()
        for t in crs_dep_times:
            self._validate(origin, dest, airline, t)
        if not crs_dep_times:
            return []
        base = {"flight_date": pd.Timestamp(flight_date), "airline": airline, "origin": origin,
                "dest": dest, **{k: (weather or {}).get(k) for k in WEATHER}}
        X = self.builder.transform(pd.DataFrame([{**base, "crs_dep_time": t} for t in crs_dep_times]))
        return [float(p) for p in self.model.predict_proba(X)[:, 1]]

    def predict(self, origin: str, dest: str, airline: str, flight_date: date,
                crs_dep_time: int, weather: dict | None = None) -> DelayPrediction:
        origin, dest, airline = origin.strip().upper(), dest.strip().upper(), airline.strip().upper()
        self._validate(origin, dest, airline, crs_dep_time)   # geçersiz girdi için METAR çekilmez
        if weather is None:
            weather = get_metar(origin)  # canlı METAR; alınamazsa model eksik değerle çalışır
        proba = self.predict_times(origin, dest, airline, flight_date, [crs_dep_time], weather)[0]

        weather_summary = None if weather is None else {
            k: (v.isoformat() if hasattr(v, "isoformat") else v)
            for k, v in weather.items() if k in {"station", "valid_utc", "raw", *WEATHER}
        }
        return DelayPrediction(probability=proba, is_delayed=proba >= self.threshold,
                               threshold=self.threshold, weather=weather_summary,
                               model_version=self.version)
