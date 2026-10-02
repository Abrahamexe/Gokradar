"""Neon PostgreSQL bağlantısı.

- pooled=False → DATABASE_URL (doğrudan): tablo oluşturma, toplu yükleme, ingest.
- pooled=True  → DATABASE_URL_POOLED (pooler): Flask API.
"""
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url

import config


def _ensure_ssl(url: str) -> str:
    """Adreste sslmode yoksa sslmode=require ekler; varsa olduğu gibi bırakır."""
    parsed = make_url(url)
    if "sslmode" not in parsed.query:
        parsed = parsed.update_query_dict({"sslmode": "require"})
    return parsed.render_as_string(hide_password=False)


def get_engine(pooled: bool = False) -> Engine:
    name = "DATABASE_URL_POOLED" if pooled else "DATABASE_URL"
    url = config.DATABASE_URL_POOLED if pooled else config.DATABASE_URL
    if not url:
        raise RuntimeError(f"{name} .env dosyasında tanımlı değil.")
    return create_engine(_ensure_ssl(url), pool_pre_ping=True)
