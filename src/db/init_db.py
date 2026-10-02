"""Tabloları ve indeksleri oluşturur (varsa dokunmaz).

Çalıştırma (proje kökünden):  python -m src.db.init_db
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.db.connection import get_engine  # noqa: E402
from src.db.models import Base  # noqa: E402


def create_tables() -> None:
    engine = get_engine()  # doğrudan bağlantı (DDL)
    Base.metadata.create_all(engine, checkfirst=True)
    engine.dispose()


if __name__ == "__main__":
    create_tables()
    print("Tablolar hazır:", ", ".join(Base.metadata.tables))
