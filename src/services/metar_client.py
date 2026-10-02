"""aviationweather.gov Data API'den güncel METAR çeker (anahtar gerekmez).

Dönen alanlar geçmiş veriyle (load_weather) aynı biçimdedir:
temp_c, wind_kt, gust_kt, visibility_mi, ceiling_ft, precip.
"""
import threading
import time
from datetime import datetime, timezone

import requests

from src.features.weather import ceiling_ft, has_precip

METAR_URL = "https://aviationweather.gov/api/data/metar"


class MetarError(RuntimeError):
    pass


def to_icao(code: str) -> str:
    """IATA (ATL) → ICAO (KATL). Kapsamdaki havalimanları ABD anakarasında olduğu için K öneki yeterli."""
    code = code.strip().upper()
    return f"K{code}" if len(code) == 3 else code


def _visibility(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, str):  # "10+" gibi
        value = value.rstrip("+")
    try:
        return float(value)
    except ValueError:
        return None


def normalize(obs: dict) -> dict:
    clouds = obs.get("clouds") or []
    return {
        "station": obs.get("icaoId"),
        "valid_utc": datetime.fromtimestamp(obs["obsTime"], tz=timezone.utc) if obs.get("obsTime") else None,
        "temp_c": obs.get("temp"),
        "wind_kt": obs.get("wspd"),
        "gust_kt": obs.get("wgst"),
        "visibility_mi": _visibility(obs.get("visib")),
        "ceiling_ft": ceiling_ft((c.get("cover"), c.get("base")) for c in clouds),
        "precip": has_precip(obs.get("wxString")),
        "wxcodes": obs.get("wxString"),
        "raw": obs.get("rawOb"),
    }


def get_metars(stations: list[str], timeout_s: float = 15.0) -> dict[str, dict]:
    """Birden çok istasyonun en güncel METAR'ı. Anahtar: istasyon ICAO kodu."""
    ids = ",".join(to_icao(s) for s in stations)
    try:
        resp = requests.get(METAR_URL, params={"ids": ids, "format": "json"}, timeout=timeout_s)
    except requests.RequestException as exc:
        raise MetarError(f"aviationweather.gov erişilemedi: {exc}") from exc
    if resp.status_code == 204 or not resp.text.strip():
        return {}
    if resp.status_code != 200:
        raise MetarError(f"aviationweather.gov: HTTP {resp.status_code} {resp.text[:200]}")
    latest: dict[str, dict] = {}
    for obs in resp.json():
        station = obs.get("icaoId")
        if station and (station not in latest or obs.get("obsTime", 0) > latest[station].get("obsTime", 0)):
            latest[station] = obs
    return {station: normalize(obs) for station, obs in latest.items()}


def get_metar(station: str, timeout_s: float = 15.0) -> dict | None:
    return get_metars([station], timeout_s).get(to_icao(station))


class MetarCache:
    """İstasyon başına TTL önbellek; aviationweather.gov'a gereksiz istek atılmaz.
    Erişim hatasında varsa eski değer, yoksa None döner (tahmin eksik hava verisiyle çalışır)."""

    def __init__(self, ttl_s: float, fetch=get_metar, clock=None):
        self.ttl_s = ttl_s
        self._fetch = fetch
        self._clock = clock or time.monotonic
        self._data: dict[str, tuple[float, dict | None]] = {}
        self._lock = threading.Lock()                       # yalnızca sözlük erişimi için
        self._key_locks: dict[str, threading.Lock] = {}     # istasyon başına tek ağ isteği

    def _fresh(self, key: str) -> tuple[tuple[float, dict | None] | None, bool]:
        with self._lock:
            hit = self._data.get(key)
        return hit, bool(hit and self._clock() - hit[0] < self.ttl_s)

    def get(self, station: str) -> dict | None:
        key = to_icao(station)
        hit, fresh = self._fresh(key)
        if fresh:
            return hit[1]
        with self._lock:
            key_lock = self._key_locks.setdefault(key, threading.Lock())
        # Ağ isteği genel kilit dışında yapılır: yavaş bir istasyon diğerlerini bekletmez;
        # aynı istasyon için eşzamanlı istekler tek üst isteği paylaşır.
        with key_lock:
            hit, fresh = self._fresh(key)
            if fresh:
                return hit[1]
            try:
                value = self._fetch(key)
            except MetarError:
                return hit[1] if hit else None
            with self._lock:
                self._data[key] = (self._clock(), value)
            return value
