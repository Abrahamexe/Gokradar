"""SQLAlchemy tablo tanımları.

Referans tablolar (Faz 1 Parquet'lerinden toplu yüklenir):
  flights_hist, weather_hourly, aircraft
Kayıt tabloları (API tarafından yazılır):
  delay_predictions, image_classifications
"""
from datetime import date, datetime

from sqlalchemy import (BigInteger, Boolean, Date, DateTime, Float, Index, SmallInteger,
                        String, Text, func)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class FlightHist(Base):
    """BTS On-Time Performance — hedef havalimanlarından kalkan, iptal edilmemiş uçuşlar."""
    __tablename__ = "flights_hist"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    flight_date: Mapped[date] = mapped_column(Date, nullable=False)
    airline: Mapped[str] = mapped_column(String(3), nullable=False)
    flight_number: Mapped[int] = mapped_column(nullable=False)
    origin: Mapped[str] = mapped_column(String(4), nullable=False)
    dest: Mapped[str] = mapped_column(String(4), nullable=False)
    crs_dep_time: Mapped[int] = mapped_column(SmallInteger, nullable=False,
                                              comment="Planlanan kalkış, yerel saat (HHMM)")
    distance: Mapped[float] = mapped_column(Float(24), nullable=False, comment="mil")
    dep_del15: Mapped[bool] = mapped_column(Boolean, nullable=False,
                                            comment="Kalkış gecikmesi >= 15 dk")

    __table_args__ = (
        Index("ix_flights_hist_origin_date_time", "origin", "flight_date", "crs_dep_time"),
    )


class WeatherHourly(Base):
    """Saatlik METAR (IEM). station = IATA kodu (ör. ATL)."""
    __tablename__ = "weather_hourly"

    station: Mapped[str] = mapped_column(String(4), primary_key=True)
    valid_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    temp_c: Mapped[float | None] = mapped_column(Float(24))
    wind_kt: Mapped[float | None] = mapped_column(Float(24))
    gust_kt: Mapped[float | None] = mapped_column(Float(24), comment="NULL = hamle yok")
    visibility_mi: Mapped[float | None] = mapped_column(Float(24))
    ceiling_ft: Mapped[float | None] = mapped_column(Float(24), comment="NULL = tavan yok (sınırsız)")
    precip: Mapped[bool] = mapped_column(Boolean, nullable=False)
    wxcodes: Mapped[str | None] = mapped_column(Text)
    # (station, valid_utc) birincil anahtarı havalimanı + zaman sorgularının indeksidir.


class Aircraft(Base):
    """OpenSky aircraft database — ICAO24 → tescil, tip, operatör.
    Kaynak veri kirli olabildiği için (ör. operator_icao alanında isim) metin alanları Text."""
    __tablename__ = "aircraft"

    icao24: Mapped[str] = mapped_column(String(6), primary_key=True)
    registration: Mapped[str | None] = mapped_column(Text)
    typecode: Mapped[str | None] = mapped_column(Text)
    manufacturer: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    operator: Mapped[str | None] = mapped_column(Text)
    operator_icao: Mapped[str | None] = mapped_column(Text)
    operator_callsign: Mapped[str | None] = mapped_column(Text)
    owner: Mapped[str | None] = mapped_column(Text)
    aircraft_class: Mapped[str | None] = mapped_column(Text)
    built: Mapped[date | None] = mapped_column(Date)


class DelayPrediction(Base):
    __tablename__ = "delay_predictions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now())
    origin: Mapped[str] = mapped_column(String(4), nullable=False)
    dest: Mapped[str] = mapped_column(String(4), nullable=False)
    airline: Mapped[str] = mapped_column(String(3), nullable=False)
    flight_date: Mapped[date] = mapped_column(Date, nullable=False)
    crs_dep_time: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    probability: Mapped[float] = mapped_column(Float(24), nullable=False)
    weather: Mapped[dict | None] = mapped_column(JSONB, comment="Kullanılan hava durumu özeti")
    model_version: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_delay_predictions_created_at", "created_at"),)


class ImageClassification(Base):
    __tablename__ = "image_classifications"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now())
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    predictions: Mapped[list] = mapped_column(JSONB, nullable=False,
                                              comment='Top-3: [{"label": ..., "score": ...}, ...]')
    top1_label: Mapped[str] = mapped_column(Text, nullable=False)
    top1_score: Mapped[float] = mapped_column(Float(24), nullable=False)
    model_version: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_image_classifications_created_at", "created_at"),)
