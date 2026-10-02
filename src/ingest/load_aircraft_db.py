"""OpenSky aircraft database CSV'sini indirir, ICAO24 → tip/tescil/operatör
tablosunu temizler ve data/processed/ altına Parquet olarak kaydeder.
(Neon'a yükleme Faz 2'de yapılır.)

OpenSky S3 deposundaki en güncel `aircraft-database-complete-YYYY-MM.csv` dosyası
otomatik bulunur; ham dosya data/raw/aircraft/ altında önbelleklenir.

Çalıştırma (proje kökünden):  python -m src.ingest.load_aircraft_db
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd  # noqa: E402
import requests  # noqa: E402

import config  # noqa: E402

S3_BUCKET = "https://s3.opensky-network.org/data-samples"
PREFIX = "metadata/aircraft-database-complete-"
OUTPUT_PATH = config.PROCESSED_DIR / "aircraft.parquet"

# OpenSky kolon adı -> projede kullanılan ad
COLUMNS = {
    "icao24": "icao24",
    "registration": "registration",
    "typecode": "typecode",
    "manufacturerName": "manufacturer",
    "model": "model",
    "operator": "operator",
    "operatorIcao": "operator_icao",
    "operatorCallsign": "operator_callsign",
    "owner": "owner",
    "icaoAircraftClass": "aircraft_class",
    "built": "built",
}


def latest_key() -> str:
    """S3 listesinden en güncel aylık dosyanın anahtarını bulur."""
    resp = requests.get(S3_BUCKET, params={"list-type": 2, "prefix": PREFIX}, timeout=30)
    resp.raise_for_status()
    keys = re.findall(r"<Key>([^<]+\.csv)</Key>", resp.text)
    if not keys:
        raise RuntimeError("OpenSky S3 deposunda aircraft database dosyası bulunamadı.")
    return max(keys)  # YYYY-MM adlandırması sözlük sırasıyla kronolojik


def download(key: str) -> Path:
    path = config.AIRCRAFT_DIR / Path(key).name
    if path.exists():
        print(f"Önbellekte: {path.name}")
        return path
    config.AIRCRAFT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    print(f"İndiriliyor: {key}")
    with requests.get(f"{S3_BUCKET}/{key}", stream=True, timeout=60) as resp:
        resp.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                f.write(chunk)
    tmp.replace(path)
    print(f"  {path.stat().st_size / 1e6:.0f} MB")
    return path


def parse(path: Path) -> pd.DataFrame:
    # Dosya tek tırnakla alıntılanmış; boş değerler '' olarak yazılmış.
    raw = pd.read_csv(path, quotechar="'", usecols=list(COLUMNS), dtype="string",
                      on_bad_lines="warn")
    df = raw.rename(columns=COLUMNS)
    for col in df.columns:
        df[col] = df[col].str.strip().replace("", pd.NA)
    df["icao24"] = df["icao24"].str.lower()
    df["typecode"] = df["typecode"].str.upper()
    df["built"] = pd.to_datetime(df["built"], errors="coerce").dt.date

    valid = df["icao24"].str.fullmatch(r"[0-9a-f]{6}").fillna(False)
    df = df[valid]
    # Tip veya tescil bilgisi olmayan kayıtlar eşleştirmede işe yaramaz
    df = df[df["typecode"].notna() | df["registration"].notna() | df["model"].notna()]
    return df.drop_duplicates(subset="icao24", keep="last").reset_index(drop=True)


def main() -> None:
    path = download(latest_key())
    df = parse(path)
    print(f"\nKayıt: {len(df):,} uçak")
    for col in ["registration", "typecode", "model", "operator", "operator_icao"]:
        print(f"  {col:15s} dolu: {df[col].notna().mean():.1%}")
    print("\nEn sık 10 tip kodu:")
    print(df["typecode"].value_counts().head(10).to_string())
    config.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUTPUT_PATH, index=False)
    print(f"\nKaydedildi: {OUTPUT_PATH.relative_to(config.BASE_DIR)}")


if __name__ == "__main__":
    main()
