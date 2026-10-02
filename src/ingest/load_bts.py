"""BTS On-Time Performance verisini okur, hedef havalimanlarından kalkan
uçuşları filtreler ve gerekli kolonları data/processed/ altına Parquet olarak kaydeder.

data/raw/bts/ içindeki tüm ZIP ve CSV dosyaları okunur; yeni ay eklemek için
dosyayı klasöre koymak yeterlidir.

Çalıştırma (proje kökünden):  python -m src.ingest.load_bts
"""
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd  # noqa: E402

import config  # noqa: E402

OUTPUT_PATH = config.PROCESSED_DIR / "bts_flights.parquet"

# BTS kolon adı -> projede kullanılan ad
COLUMNS = {
    "FlightDate": "flight_date",
    "Reporting_Airline": "airline",
    "Flight_Number_Reporting_Airline": "flight_number",
    "Origin": "origin",
    "Dest": "dest",
    "CRSDepTime": "crs_dep_time",
    "Distance": "distance",
    "Cancelled": "cancelled",
    "DepDel15": "dep_del15",
}
DTYPES = {"Reporting_Airline": "string", "Origin": "string", "Dest": "string"}


def _read_csv(source, origins: set[str]) -> pd.DataFrame:
    df = pd.read_csv(source, usecols=list(COLUMNS), dtype=DTYPES, low_memory=False)
    df = df[df["Origin"].isin(origins)]
    return df.rename(columns=COLUMNS)


def read_file(path: Path, origins: set[str]) -> pd.DataFrame:
    """Tek bir ZIP veya CSV dosyasını okur ve kalkış havalimanına göre filtreler."""
    if path.suffix.lower() == ".csv":
        return _read_csv(path, origins)
    with zipfile.ZipFile(path) as zf:
        members = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if not members:
            raise ValueError(f"{path.name} içinde CSV bulunamadı.")
        return pd.concat([_read_csv(zf.open(m), origins) for m in members], ignore_index=True)


def clean(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["flight_date"] = pd.to_datetime(df["flight_date"]).dt.date
    df["flight_number"] = df["flight_number"].astype("int32")
    df["crs_dep_time"] = df["crs_dep_time"].astype("int16")  # yerel saat, HHMM
    df["distance"] = df["distance"].astype("float32")
    df["cancelled"] = df["cancelled"].astype(bool)
    df["dep_del15"] = df["dep_del15"].astype("Int8")  # iptal edilenlerde boş
    df = df.drop_duplicates()
    return df.sort_values(["flight_date", "origin", "crs_dep_time"]).reset_index(drop=True)


def load_all(bts_dir: Path = config.BTS_DIR,
             airports: list[str] = config.TARGET_AIRPORTS) -> pd.DataFrame:
    files = sorted(p for p in bts_dir.iterdir() if p.suffix.lower() in (".zip", ".csv"))
    if not files:
        raise FileNotFoundError(f"{bts_dir} içinde ZIP/CSV dosyası yok.")
    origins = set(airports)
    parts = []
    for path in files:
        part = read_file(path, origins)
        print(f"  {path.name}: {len(part):,} satır")
        parts.append(part)
    return clean(pd.concat(parts, ignore_index=True))


def summarize(df: pd.DataFrame) -> None:
    print(f"\nToplam: {len(df):,} uçuş | tarih: {df['flight_date'].min()} → {df['flight_date'].max()}")
    print(f"İptal oranı: {df['cancelled'].mean():.2%}")
    active = df[~df["cancelled"]]
    missing = active["dep_del15"].isna().sum()
    print(f"Gecikme (≥15 dk) oranı, iptal hariç: {active['dep_del15'].mean():.2%}"
          f" | iptal olmayıp DEP_DEL15 boş: {missing:,}")
    invalid_time = (~df["crs_dep_time"].between(0, 2359) | (df["crs_dep_time"] % 100 >= 60)).sum()
    print(f"Geçersiz CRS_DEP_TIME: {invalid_time:,}")
    by_origin = df.groupby("origin").agg(
        ucus=("origin", "size"),
        iptal=("cancelled", "mean"),
        gecikme=("dep_del15", "mean"),
    ).sort_values("ucus", ascending=False)
    print("\nKalkış havalimanına göre:")
    print(by_origin.to_string(formatters={"ucus": "{:,}".format,
                                          "iptal": "{:.2%}".format,
                                          "gecikme": "{:.2%}".format}))
    print(f"\nHavayolu sayısı: {df['airline'].nunique()} | varış havalimanı sayısı: {df['dest'].nunique()}")


def main() -> None:
    print(f"BTS dosyaları okunuyor ({config.BTS_DIR.relative_to(config.BASE_DIR)}):")
    df = load_all()
    summarize(df)
    config.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUTPUT_PATH, index=False)
    print(f"\nKaydedildi: {OUTPUT_PATH.relative_to(config.BASE_DIR)}")


if __name__ == "__main__":
    main()
