"""API'nin veritabanı işlemleri. Engine dışarıdan verilir (API'de DATABASE_URL_POOLED)."""
from datetime import date, datetime

from sqlalchemy import bindparam, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from src.db.models import Aircraft, DelayPrediction, ImageClassification, NasStatusSnapshot

AIRCRAFT_FIELDS = ["icao24", "registration", "typecode", "manufacturer", "model",
                   "operator", "operator_icao", "operator_callsign", "owner", "aircraft_class", "built"]


def _aircraft_dict(row: Aircraft) -> dict:
    d = {f: getattr(row, f) for f in AIRCRAFT_FIELDS}
    d["built"] = d["built"].isoformat() if d["built"] else None
    return d


class Repository:
    def __init__(self, engine: Engine):
        self.engine = engine

    def ping(self) -> bool:
        with self.engine.connect() as conn:
            return conn.execute(text("SELECT 1")).scalar_one() == 1

    def get_aircraft(self, icao24: str) -> dict | None:
        with Session(self.engine) as s:
            row = s.get(Aircraft, icao24)
            return _aircraft_dict(row) if row else None

    def get_aircraft_many(self, icao24s: list[str]) -> dict[str, dict]:
        if not icao24s:
            return {}
        stmt = select(Aircraft).where(Aircraft.icao24.in_(bindparam("ids", expanding=True)))
        with Session(self.engine) as s:
            rows = s.scalars(stmt, {"ids": sorted(set(icao24s))}).all()
            return {r.icao24: _aircraft_dict(r) for r in rows}

    def get_airline_names(self) -> dict[str, str]:
        """ICAO havayolu kodu → en sık görülen operatör adı (çağrı kodu önekinden havayolu bulmak için)."""
        sql = text("""
            SELECT DISTINCT ON (code) code, operator FROM (
                SELECT upper(operator_icao) AS code, operator, count(*) AS n FROM aircraft
                WHERE operator_icao ~ '^[A-Za-z]{3}$' AND coalesce(operator, '') <> ''
                GROUP BY 1, 2) t
            ORDER BY code, n DESC""")
        with self.engine.connect() as conn:
            return {code: name for code, name in conn.execute(sql)}

    def save_delay_prediction(self, *, origin: str, dest: str, airline: str, flight_date: date,
                              crs_dep_time: int, probability: float, weather: dict | None,
                              model_version: str) -> int:
        with Session(self.engine) as s, s.begin():
            row = DelayPrediction(origin=origin, dest=dest, airline=airline, flight_date=flight_date,
                                  crs_dep_time=crs_dep_time, probability=probability,
                                  weather=weather, model_version=model_version)
            s.add(row)
            s.flush()
            return row.id

    def save_image_classification(self, *, file_path: str, predictions: list[dict],
                                  model_version: str) -> int:
        with Session(self.engine) as s, s.begin():
            row = ImageClassification(file_path=file_path, predictions=predictions,
                                      top1_label=predictions[0]["label"],
                                      top1_score=predictions[0]["score"], model_version=model_version)
            s.add(row)
            s.flush()
            return row.id

    def save_nas_snapshot(self, *, fetched_at: datetime, events: dict, raw_xml: str) -> None:
        with Session(self.engine) as s, s.begin():
            s.add(NasStatusSnapshot(fetched_at=fetched_at, events=events, raw_xml=raw_xml))
