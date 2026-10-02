"""Çağrı kodundan uçuş rotası (kalkış/varış havalimanı) — adsbdb.com (ücretsiz, anahtarsız).

Rotalar tarife verisinden gelir; aynı çağrı kodu farklı günlerde farklı uçuş olabilir. Bu yüzden
uçağın anlık konumu verilirse rotanın makul olup olmadığı (konum, kalkış–varış arasındaki büyük
daire yolunun yakınında mı) ayrıca hesaplanır.

Sonuçlar bellekte tutulur: bulunan rota 6 saat, bulunamayan 1 saat, hata 2 dakika.
"""
import logging
import math
import threading
import time

import requests

import config

log = logging.getLogger(__name__)

EARTH_KM = 6371.0
FOUND_TTL_S, MISSING_TTL_S, ERROR_TTL_S = 6 * 3600, 3600, 120


class RouteLookupError(RuntimeError):
    pass


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_KM * math.asin(math.sqrt(min(1.0, a)))


def progress(route: dict, lat: float, lon: float) -> dict:
    """Konuma göre uçulan/kalan mesafe ve rotanın makul olup olmadığı."""
    o, d = route["origin"], route["destination"]
    total = haversine_km(o["lat"], o["lon"], d["lat"], d["lon"])
    flown = haversine_km(o["lat"], o["lon"], lat, lon)
    left = haversine_km(lat, lon, d["lat"], d["lon"])
    # Konum yola yakınsa flown + left ≈ total olur; sapma payı: %25 + 250 km
    plausible = flown + left <= total * 1.25 + 250
    frac = flown / (flown + left) if flown + left > 0 else 0.0
    return {"total_km": round(total), "flown_km": round(flown), "remaining_km": round(left),
            "fraction": round(frac, 4), "plausible": plausible}


def _airport(a: dict) -> dict:
    return {"iata": a.get("iata_code"), "icao": a.get("icao_code"), "name": a.get("name"),
            "city": a.get("municipality"), "country": a.get("country_name"),
            "lat": a.get("latitude"), "lon": a.get("longitude")}


class RouteLookup:
    def __init__(self, base_url: str = config.ROUTE_API_URL, timeout_s: float = 8.0, fetch=None,
                 clock=time.monotonic):
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self._session = requests.Session()
        self._fetch = fetch or self._fetch_http
        self._clock = clock
        self._cache: dict[str, tuple[float, dict | None | Exception]] = {}
        self._lock = threading.Lock()

    def _fetch_http(self, callsign: str) -> dict | None:
        try:
            resp = self._session.get(f"{self.base_url}/v0/callsign/{callsign}", timeout=self.timeout_s,
                                     headers={"User-Agent": "gokradar/1.0"})
        except requests.RequestException as exc:
            raise RouteLookupError(f"Rota servisine ulaşılamadı: {exc}") from exc
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise RouteLookupError(f"Rota servisi HTTP {resp.status_code}")
        body = resp.json().get("response")
        fr = body.get("flightroute") if isinstance(body, dict) else None
        if not fr or not fr.get("origin") or not fr.get("destination"):
            return None
        airline = fr.get("airline") or {}
        return {"callsign": fr.get("callsign") or callsign,
                "callsign_iata": fr.get("callsign_iata"),
                "airline": {"name": airline.get("name"), "icao": airline.get("icao"),
                            "iata": airline.get("iata"), "country": airline.get("country")} if airline else None,
                "origin": _airport(fr["origin"]), "destination": _airport(fr["destination"])}

    def get(self, callsign: str) -> dict | None:
        """Rota ya da None (bilinmiyor). Servis hatasında RouteLookupError."""
        key = callsign.strip().upper()
        now = self._clock()
        with self._lock:
            hit = self._cache.get(key)
        if hit and hit[0] > now:
            if isinstance(hit[1], Exception):
                raise hit[1]
            return hit[1]
        try:
            route = self._fetch(key)
        except RouteLookupError as exc:
            log.warning("Rota alınamadı (%s): %s", key, exc)
            with self._lock:
                self._cache[key] = (now + ERROR_TTL_S, exc)
            raise
        with self._lock:
            if len(self._cache) > 20_000:
                self._cache.clear()
            self._cache[key] = (now + (FOUND_TTL_S if route else MISSING_TTL_S), route)
        return route
