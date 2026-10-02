"""Faz 1 Parquet dosyalarını Neon'a PostgreSQL COPY ile toplu yükler.

Her tablo tek transaction içinde boşaltılıp (TRUNCATE) yeniden doldurulur; yani
script tekrar çalıştırılabilir ve yeni ay eklendiğinde Parquet'in tamamını yansıtır.
İptal edilen uçuşlar flights_hist'e alınmaz.

Çalıştırma (proje kökünden):
    python -m src.ingest.load_to_db                   # tüm tablolar
    python -m src.ingest.load_to_db flights weather   # yalnızca seçilenler
"""
import argparse
import io
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd  # noqa: E402
from sqlalchemy import text  # noqa: E402

from src.db.connection import get_engine  # noqa: E402
from src.db.init_db import create_tables  # noqa: E402
from src.ingest.load_aircraft_db import OUTPUT_PATH as AIRCRAFT_PATH  # noqa: E402
from src.ingest.load_bts import OUTPUT_PATH as BTS_PATH  # noqa: E402
from src.ingest.load_weather import OUTPUT_PATH as WEATHER_PATH  # noqa: E402

CHUNK_ROWS = 100_000


def prepare_flights() -> pd.DataFrame:
    df = pd.read_parquet(BTS_PATH)
    df = df[~df["cancelled"]].drop(columns="cancelled")
    df["dep_del15"] = df["dep_del15"].astype(bool)
    return df


def prepare_weather() -> pd.DataFrame:
    return pd.read_parquet(WEATHER_PATH)


def prepare_aircraft() -> pd.DataFrame:
    return pd.read_parquet(AIRCRAFT_PATH)


# ad -> (tablo, Parquet hazırlama fonksiyonu, kaynak dosya)
TABLES = {
    "flights": ("flights_hist", prepare_flights, BTS_PATH),
    "weather": ("weather_hourly", prepare_weather, WEATHER_PATH),
    "aircraft": ("aircraft", prepare_aircraft, AIRCRAFT_PATH),
}


def copy_dataframe(cursor, table: str, df: pd.DataFrame) -> None:
    """DataFrame'i CSV olarak COPY FROM STDIN ile yükler (boş alan = NULL)."""
    cols = ", ".join(df.columns)
    sql = f"COPY {table} ({cols}) FROM STDIN WITH (FORMAT csv)"
    for start in range(0, len(df), CHUNK_ROWS):
        buf = io.StringIO()
        df.iloc[start:start + CHUNK_ROWS].to_csv(buf, index=False, header=False)
        buf.seek(0)
        cursor.copy_expert(sql, buf)


def load_table(engine, name: str) -> None:
    table, prepare, source = TABLES[name]
    if not source.exists():
        raise FileNotFoundError(f"{source.name} yok; önce Faz 1 scriptini çalıştırın.")
    df = prepare()
    t0 = time.perf_counter()
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {table} RESTART IDENTITY"))
        with conn.connection.dbapi_connection.cursor() as cur:
            copy_dataframe(cur, table, df)
        count = conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text(f"ANALYZE {table}"))
    if count != len(df):
        raise RuntimeError(f"{table}: {len(df):,} satır gönderildi ama tabloda {count:,} var.")
    print(f"  {table:15s} {count:>9,} satır  ({time.perf_counter() - t0:.1f} sn)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tables", nargs="*", metavar="TABLO",
                        help=f"yüklenecek tablolar: {', '.join(TABLES)} (varsayılan: hepsi)")
    args = parser.parse_args()
    unknown = set(args.tables) - set(TABLES)
    if unknown:
        parser.error(f"bilinmeyen tablo: {', '.join(sorted(unknown))}")
    args.tables = args.tables or list(TABLES)

    create_tables()
    engine = get_engine()  # doğrudan bağlantı (toplu yükleme)
    print("Neon'a yükleniyor (COPY):")
    for name in args.tables:
        load_table(engine, name)
    engine.dispose()


if __name__ == "__main__":
    main()
