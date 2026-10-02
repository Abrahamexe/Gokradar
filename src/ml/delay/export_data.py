"""Gecikme modeli eğitim verisini Neon'dan bir kez çekip Parquet olarak kaydeder.

Eğitim bu dosyalardan okunur; dosyalar varsa Neon'a tekrar gidilmez
(`--refresh` ile yeniden çekilir).

Çalıştırma (proje kökünden):  python -m src.ml.delay.export_data [--refresh]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import pandas as pd  # noqa: E402

import config  # noqa: E402
from src.db.connection import get_engine  # noqa: E402

FLIGHTS_PATH = config.PROCESSED_DIR / "delay_train_flights.parquet"
WEATHER_PATH = config.PROCESSED_DIR / "delay_train_weather.parquet"

# Yalnızca kalkıştan önce bilinen kolonlar + hedef. Gerçekleşen kalkış saati,
# taxi süreleri ve gecikme sebepleri flights_hist'te zaten yok.
FLIGHTS_SQL = """
    SELECT flight_date, airline, flight_number, origin, dest, crs_dep_time, distance, dep_del15
    FROM flights_hist
    ORDER BY flight_date, origin, crs_dep_time
"""
WEATHER_SQL = """
    SELECT station, valid_utc, temp_c, wind_kt, gust_kt, visibility_mi, ceiling_ft, precip
    FROM weather_hourly
    ORDER BY station, valid_utc
"""


def export(refresh: bool = False) -> tuple[Path, Path]:
    if FLIGHTS_PATH.exists() and WEATHER_PATH.exists() and not refresh:
        print("Eğitim verisi zaten var (yeniden çekmek için --refresh).")
        return FLIGHTS_PATH, WEATHER_PATH
    engine = get_engine()
    with engine.connect() as conn:
        flights = pd.read_sql(FLIGHTS_SQL, conn)
        weather = pd.read_sql(WEATHER_SQL, conn)
    engine.dispose()
    flights["flight_date"] = pd.to_datetime(flights["flight_date"])
    weather["valid_utc"] = pd.to_datetime(weather["valid_utc"], utc=True)
    config.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    flights.to_parquet(FLIGHTS_PATH, index=False)
    weather.to_parquet(WEATHER_PATH, index=False)
    print(f"Neon'dan çekildi: {len(flights):,} uçuş, {len(weather):,} METAR")
    return FLIGHTS_PATH, WEATHER_PATH


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true", help="Parquet varsa bile Neon'dan yeniden çek")
    export(parser.parse_args().refresh)
