"""Hedef havalimanları için Iowa Environmental Mesonet (IEM) arşivinden saatlik
METAR verisini indirir ve ortak biçimde data/processed/ altına Parquet olarak kaydeder.

Tarih aralığı BTS verisinden alınır (önce load_bts çalıştırılmalı). BTS saatleri
yerel, METAR saatleri UTC olduğu için aralık her iki uçta da 1 gün genişletilir.
İndirilen ham CSV'ler data/raw/weather/ altında önbelleklenir.

Çalıştırma (proje kökünden):
  python -m src.ingest.load_weather          # kalkış havalimanları (TARGET_AIRPORTS)
  python -m src.ingest.load_weather --dest   # BTS'deki tüm varış havalimanları
"""
import hashlib
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd  # noqa: E402
import requests  # noqa: E402

import config  # noqa: E402
from src.features.weather import ceiling_ft, f_to_c, has_precip  # noqa: E402
from src.ingest.load_bts import OUTPUT_PATH as BTS_PATH  # noqa: E402
from src.services.metar_client import NON_CONTIGUOUS_ICAO  # noqa: E402

IEM_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
OUTPUT_PATH = config.PROCESSED_DIR / "weather_hourly.parquet"
DEST_OUTPUT_PATH = config.PROCESSED_DIR / "weather_dest_hourly.parquet"
FIELDS = ["tmpf", "sknt", "gust", "vsby", "wxcodes",
          "skyc1", "skyc2", "skyc3", "skyc4", "skyl1", "skyl2", "skyl3", "skyl4"]


def bts_date_range() -> tuple[date, date]:
    if not BTS_PATH.exists():
        raise FileNotFoundError(f"{BTS_PATH.name} yok; önce `python -m src.ingest.load_bts` çalıştırın.")
    dates = pd.read_parquet(BTS_PATH, columns=["flight_date"])["flight_date"]
    return dates.min() - timedelta(days=1), dates.max() + timedelta(days=1)


def download_station(station: str, start: date, end: date) -> Path:
    """Bir istasyonun [start, end] aralığındaki rutin (saatlik) METAR'larını indirir."""
    path = config.WEATHER_DIR / f"{station}_{start:%Y%m%d}_{end:%Y%m%d}.csv"
    if path.exists():
        print(f"  {station}: önbellekte ({path.name})")
        return path
    end_excl = end + timedelta(days=1)  # IEM bitiş tarihi hariç tutulur
    params = {
        "station": station, "data": FIELDS,
        "year1": start.year, "month1": start.month, "day1": start.day,
        "year2": end_excl.year, "month2": end_excl.month, "day2": end_excl.day,
        "tz": "Etc/UTC", "format": "onlycomma", "latlon": "no",
        "missing": "M", "trace": "T", "direct": "no",
        "report_type": 3,  # 3 = rutin saatlik METAR
    }
    for attempt in range(3):
        resp = requests.get(IEM_URL, params=params, timeout=120)
        if resp.status_code == 200 and resp.text.startswith("station,"):
            break
        time.sleep(5 * (attempt + 1))
    else:
        raise RuntimeError(f"{station} indirilemedi: HTTP {resp.status_code} {resp.text[:200]}")
    config.WEATHER_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(resp.text, encoding="utf-8")
    print(f"  {station}: indirildi ({resp.text.count(chr(10)) - 1} satır)")
    return path


def parse_iem_csv(path: Path) -> pd.DataFrame:
    raw = pd.read_csv(path, na_values=["M"], dtype={"station": "string", "wxcodes": "string"})
    covers = [f"skyc{i}" for i in range(1, 5)]
    bases = [f"skyl{i}" for i in range(1, 5)]
    df = pd.DataFrame({
        "station": raw["station"],
        "valid_utc": pd.to_datetime(raw["valid"], utc=True),
        "temp_c": raw["tmpf"].map(lambda v: f_to_c(v) if pd.notna(v) else None).astype("float32"),
        "wind_kt": raw["sknt"].astype("float32"),
        "gust_kt": raw["gust"].astype("float32"),
        "visibility_mi": raw["vsby"].astype("float32"),
        "ceiling_ft": [ceiling_ft(zip(c, b)) for c, b in zip(raw[covers].values, raw[bases].values)],
        "precip": raw["wxcodes"].map(has_precip).astype(bool),
        "wxcodes": raw["wxcodes"],
    })
    df["ceiling_ft"] = df["ceiling_ft"].astype("float32")  # NaN = tavan yok
    return df.drop_duplicates(subset=["station", "valid_utc"])


def download_batch(stations: list[str], start: date, end: date) -> Path:
    """Birden çok istasyon tek istekte (IEM 'station' parametresini tekrar kabul eder)."""
    key = hashlib.sha1(",".join(sorted(stations)).encode()).hexdigest()[:10]
    path = config.WEATHER_DIR / f"dest_{key}_{start:%Y%m%d}_{end:%Y%m%d}.csv"
    if path.exists():
        return path
    end_excl = end + timedelta(days=1)
    params = [("station", s) for s in stations] + [("data", f) for f in FIELDS] + list({
        "year1": start.year, "month1": start.month, "day1": start.day,
        "year2": end_excl.year, "month2": end_excl.month, "day2": end_excl.day,
        "tz": "Etc/UTC", "format": "onlycomma", "latlon": "no",
        "missing": "M", "trace": "T", "direct": "no", "report_type": 3,
    }.items())
    for attempt in range(4):
        resp = requests.get(IEM_URL, params=params, timeout=300)
        if resp.status_code == 200 and resp.text.startswith("station,"):
            break
        time.sleep(10 * (attempt + 1))
    else:
        raise RuntimeError(f"İndirilemedi: HTTP {resp.status_code} {resp.text[:200]}")
    config.WEATHER_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(resp.text, encoding="utf-8")
    return path


def main_dest(batch_size: int = 25) -> None:
    """BTS verisindeki tüm varış havalimanlarının METAR'ı (gecikme modelinin varış hava özellikleri)."""
    start, end = bts_date_range()
    dests = sorted(pd.read_parquet(BTS_PATH, columns=["dest"])["dest"].unique())
    print(f"IEM METAR (varış): {start} → {end}, {len(dests)} havalimanı, {batch_size}'li gruplar")
    frames = []
    for i in range(0, len(dests), batch_size):
        frames.append(parse_iem_csv(download_batch(dests[i:i + batch_size], start, end)))
        print(f"  {min(i + batch_size, len(dests))}/{len(dests)}", flush=True)
        time.sleep(1)
    # IEM, ABD anakarası dışındaki istasyonları 4 harfli ICAO koduyla tutar
    retry = {NON_CONTIGUOUS_ICAO[d]: d for d in dests
             if d in NON_CONTIGUOUS_ICAO and not any(d in set(f["station"]) for f in frames)}
    if retry:
        extra = parse_iem_csv(download_batch(sorted(retry), start, end))
        extra["station"] = extra["station"].map(lambda s: retry.get(s, s))
        frames.append(extra)
    df = pd.concat(frames, ignore_index=True).sort_values(["station", "valid_utc"])
    missing = sorted(set(dests) - set(df["station"]))
    print(f"Toplam: {len(df):,} gözlem, {df['station'].nunique()} istasyon"
          + (f" | METAR'ı bulunamayan: {', '.join(missing)}" if missing else ""))
    df.to_parquet(DEST_OUTPUT_PATH, index=False)
    print(f"Kaydedildi: {DEST_OUTPUT_PATH.relative_to(config.BASE_DIR)}")


def main() -> None:
    if "--dest" in sys.argv:
        return main_dest()
    start, end = bts_date_range()
    print(f"IEM METAR indiriliyor: {start} → {end} (UTC), {len(config.TARGET_AIRPORTS)} havalimanı")
    frames = []
    for station in config.TARGET_AIRPORTS:
        frames.append(parse_iem_csv(download_station(station, start, end)))
        time.sleep(1)  # IEM'e nazik davran
    df = pd.concat(frames, ignore_index=True).sort_values(["station", "valid_utc"])

    print(f"\nToplam: {len(df):,} gözlem")
    summary = df.groupby("station").agg(
        gozlem=("valid_utc", "size"),
        ilk=("valid_utc", "min"),
        son=("valid_utc", "max"),
        eksik_sicaklik=("temp_c", lambda s: s.isna().sum()),
        eksik_gorus=("visibility_mi", lambda s: s.isna().sum()),
        yagisli=("precip", "mean"),
        tavanli=("ceiling_ft", lambda s: s.notna().mean()),
    )
    print(summary.to_string(formatters={"yagisli": "{:.1%}".format, "tavanli": "{:.1%}".format}))

    hours = (end - start).days * 24 + 24
    low = summary[summary["gozlem"] < 0.9 * hours]
    if not low.empty:
        print(f"\nUyarı: beklenen ~{hours} saatlik gözlemin %90'ından azı olan istasyonlar: {list(low.index)}")

    config.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUTPUT_PATH, index=False)
    print(f"\nKaydedildi: {OUTPUT_PATH.relative_to(config.BASE_DIR)}")


if __name__ == "__main__":
    main()
