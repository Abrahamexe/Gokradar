"""API girdi doğrulama ve hata tipi."""
import math
import re
from datetime import date


class ApiError(Exception):
    def __init__(self, status: int, message: str, code: str = "bad_request", details: dict | None = None):
        super().__init__(message)
        self.status, self.message, self.code, self.details = status, message, code, details

    def to_dict(self) -> dict:
        body = {"code": self.code, "message": self.message}
        if self.details:
            body["details"] = self.details
        return {"error": body}


def require_fields(data: dict, fields: list[str]) -> None:
    missing = [f for f in fields if data.get(f) in (None, "")]
    if missing:
        raise ApiError(400, f"Eksik alan(lar): {', '.join(missing)}", "missing_fields",
                       {"missing": missing})


def parse_airport(value, field: str) -> str:
    code = str(value).strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", code):
        raise ApiError(400, f"{field}: 3 harfli IATA havalimanı kodu olmalı (ör. ATL).", "invalid_airport")
    return code


def parse_airline(value) -> str:
    code = str(value).strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{2,3}", code):
        raise ApiError(400, "airline: 2-3 karakterli havayolu kodu olmalı (ör. DL).", "invalid_airline")
    return code


def parse_date(value) -> date:
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        raise ApiError(400, "date: YYYY-AA-GG biçiminde olmalı (ör. 2026-09-25).", "invalid_date") from None


def parse_dep_time(value) -> int:
    """'HH:MM', 'HHMM' veya tamsayı HHMM → HHMM tamsayı (yerel saat)."""
    text = str(value).strip()
    m = re.fullmatch(r"(\d{1,2}):?(\d{2})", text)
    if m:
        hh, mm = int(m.group(1)), int(m.group(2))
        if hh <= 23 and mm <= 59:
            return hh * 100 + mm
    raise ApiError(400, "dep_time: yerel planlanan kalkış saati 'HH:MM' biçiminde olmalı (ör. 08:30).",
                   "invalid_time")


def parse_icao24(value: str) -> str:
    code = value.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{6}", code):
        raise ApiError(400, "icao24: 6 haneli onaltılık kod olmalı (ör. a1b2c3).", "invalid_icao24")
    return code


def parse_float_args(args, names: list[str]) -> list[float]:
    missing = [n for n in names if args.get(n) in (None, "")]
    if missing:
        raise ApiError(400, f"Eksik parametre(ler): {', '.join(missing)}", "missing_params",
                       {"missing": missing})
    try:
        values = [float(args[n]) for n in names]
    except ValueError:
        raise ApiError(400, f"{', '.join(names)} sayısal olmalı.", "invalid_params") from None
    if not all(math.isfinite(v) for v in values):
        raise ApiError(400, f"{', '.join(names)} sonlu birer sayı olmalı.", "invalid_params")
    return values
