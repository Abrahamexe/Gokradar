"""OpenSky durum verisi için paylaşımlı önbellek ve kredi sayacı.

- Aynı bölge için en fazla TTL'de bir istek atılır; tüm kullanıcılar önbellekten beslenir.
  İstenen bölgeyi kapsayan taze bir kayıt varsa yeni istek atılmaz, o kayıt filtrelenir.
- Aynı anda gelen istekler için tek bir üst istek atılır (single-flight).
- İstenen bölge, farklı kullanıcıların aynı kaydı paylaşabilmesi için ızgaraya dışa doğru
  yuvarlanır; yuvarlama kredi kademesini yükseltecekse yuvarlanmaz.
- /states/all kredi maliyeti bölge alanına göre kademelidir (derecekare):
    0–25 → 1, 25–100 → 2, 100–400 → 3, 400+ veya tüm dünya → 4
- Günlük (UTC) harcanan kredi sayılır ve loglanır. Kalan kredi yedek sınırın altına inerse
  yeni istek atılmaz; varsa eski (bayat) veri işaretlenerek döner.
"""
import logging
import math
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

import config
from src.services.opensky_client import OpenSkyError, OpenSkyRateLimitError

log = logging.getLogger("opensky")

CREDIT_TIERS = [(25.0, 1), (100.0, 2), (400.0, 3)]
MAX_CREDIT_COST = 4


def credit_cost(area_sqdeg: float) -> int:
    for upper, cost in CREDIT_TIERS:
        if area_sqdeg <= upper:
            return cost
    return MAX_CREDIT_COST


class OpenSkyBudgetError(RuntimeError):
    """Günlük kredi bütçesi yetersiz ve önbellekte kullanılabilir veri yok."""


@dataclass(frozen=True)
class BBox:
    lamin: float
    lomin: float
    lamax: float
    lomax: float

    def __post_init__(self):
        if not (-90 <= self.lamin < self.lamax <= 90 and -180 <= self.lomin < self.lomax <= 180):
            raise ValueError("Geçersiz bölge: -90 ≤ lamin < lamax ≤ 90 ve -180 ≤ lomin < lomax ≤ 180 olmalı.")

    @property
    def area(self) -> float:
        return (self.lamax - self.lamin) * (self.lomax - self.lomin)

    def contains(self, other: "BBox") -> bool:
        return (self.lamin <= other.lamin and self.lomin <= other.lomin
                and self.lamax >= other.lamax and self.lomax >= other.lomax)

    def contains_point(self, lat, lon) -> bool:
        return (lat is not None and lon is not None
                and self.lamin <= lat <= self.lamax and self.lomin <= lon <= self.lomax)

    def snapped(self, grid: float) -> "BBox":
        return BBox(max(-90.0, math.floor(self.lamin / grid) * grid),
                    max(-180.0, math.floor(self.lomin / grid) * grid),
                    min(90.0, math.ceil(self.lamax / grid) * grid),
                    min(180.0, math.ceil(self.lomax / grid) * grid))

    def as_params(self) -> dict:
        return {"lamin": self.lamin, "lomin": self.lomin, "lamax": self.lamax, "lomax": self.lomax}


class CreditCounter:
    """UTC gün bazında harcanan krediyi sayar; OpenSky'ın bildirdiği kalan krediyi de tutar."""

    def __init__(self, daily_limit: int, today=lambda: datetime.now(timezone.utc).date()):
        self.daily_limit = daily_limit
        self._today = today
        self._lock = threading.Lock()
        self._day: date = today()
        self.used = 0
        self.requests = 0
        self.server_remaining: int | None = None

    def _roll(self) -> None:
        day = self._today()
        if day != self._day:
            log.info("OpenSky kredi günü kapandı: %s → %d kredi, %d istek", self._day, self.used, self.requests)
            self._day, self.used, self.requests, self.server_remaining = day, 0, 0, None

    def remaining(self) -> int:
        with self._lock:
            self._roll()
            local = self.daily_limit - self.used
            return local if self.server_remaining is None else min(local, self.server_remaining)

    def add(self, credits: int, server_remaining: int | None, bbox: "BBox") -> None:
        with self._lock:
            self._roll()
            self.used += credits
            self.requests += 1
            if server_remaining is not None:
                self.server_remaining = server_remaining
            log.info("OpenSky isteği: bölge %.1f derecekare, %d kredi | bugün %d/%d kredi, %d istek"
                     " | sunucuya göre kalan: %s", bbox.area, credits, self.used, self.daily_limit,
                     self.requests, self.server_remaining)

    def snapshot(self) -> dict:
        with self._lock:
            self._roll()
            return {"date_utc": self._day.isoformat(), "credits_used": self.used,
                    "requests": self.requests, "daily_limit": self.daily_limit,
                    "server_remaining": self.server_remaining}


@dataclass
class _Entry:
    bbox: BBox
    fetched_at: float
    time: int | None
    states: list[dict] = field(default_factory=list)


class OpenSkyCache:
    def __init__(self, client, ttl_s: float = config.OPENSKY_CACHE_TTL_S,
                 grid_deg: float = config.OPENSKY_GRID_DEG,
                 max_area_sqdeg: float = config.OPENSKY_MAX_AREA_SQDEG,
                 counter: CreditCounter | None = None,
                 reserve: int = config.OPENSKY_CREDIT_RESERVE, clock=time.monotonic):
        self.client = client
        self.ttl_s = ttl_s
        self.grid_deg = grid_deg
        self.max_area_sqdeg = max_area_sqdeg
        self.counter = counter or CreditCounter(config.OPENSKY_DAILY_CREDITS)
        self.reserve = reserve
        self._clock = clock
        self._entries: dict[BBox, _Entry] = {}
        self._lock = threading.Lock()
        self._key_locks: dict[BBox, threading.Lock] = {}

    def fetch_bbox(self, bbox: BBox) -> BBox:
        """Paylaşım için ızgaraya yuvarlanmış bölge; kredi kademesi artacaksa istenen bölge."""
        snapped = bbox.snapped(self.grid_deg)
        return snapped if credit_cost(snapped.area) <= credit_cost(bbox.area) else bbox

    def _find(self, bbox: BBox, fresh_only: bool) -> _Entry | None:
        now = self._clock()
        with self._lock:
            candidates = [e for e in self._entries.values() if e.bbox.contains(bbox)
                          and (not fresh_only or now - e.fetched_at < self.ttl_s)]
        return max(candidates, key=lambda e: e.fetched_at, default=None)

    def _result(self, entry: _Entry, bbox: BBox, stale: bool, credits: int) -> tuple[list[dict], dict]:
        states = [s for s in entry.states if bbox.contains_point(s.get("latitude"), s.get("longitude"))]
        return states, {"opensky_time": entry.time, "cached": credits == 0,
                        "age_s": round(self._clock() - entry.fetched_at, 1), "stale": stale,
                        "credits_spent": credits, "fetched_bbox": entry.bbox.as_params()}

    def _prune(self) -> None:
        cutoff = self._clock() - 10 * self.ttl_s
        with self._lock:
            for key in [k for k, e in self._entries.items() if e.fetched_at < cutoff]:
                del self._entries[key]

    def get_states(self, bbox: BBox) -> tuple[list[dict], dict]:
        if bbox.area > self.max_area_sqdeg:
            raise ValueError(f"Bölge çok büyük: {bbox.area:.0f} derecekare "
                             f"(en fazla {self.max_area_sqdeg:.0f}).")
        entry = self._find(bbox, fresh_only=True)
        if entry:
            return self._result(entry, bbox, stale=False, credits=0)

        key = self.fetch_bbox(bbox)
        with self._lock:
            key_lock = self._key_locks.setdefault(key, threading.Lock())
        with key_lock:  # aynı bölge için tek üst istek
            entry = self._find(bbox, fresh_only=True)  # beklerken başka istek doldurmuş olabilir
            if entry:
                return self._result(entry, bbox, stale=False, credits=0)

            cost = credit_cost(key.area)
            if self.counter.remaining() - cost < self.reserve:
                return self._stale_or_raise(bbox, "Günlük OpenSky kredi bütçesi doldu.")
            try:
                resp = self.client.get_states(**key.as_params())
            except OpenSkyRateLimitError as exc:
                return self._stale_or_raise(bbox, "OpenSky rate limit aşıldı.", error=exc)
            except OpenSkyError as exc:  # ağ hatası, zaman aşımı, 5xx
                return self._stale_or_raise(bbox, f"OpenSky hatası: {exc}", error=exc)
            self.counter.add(cost, resp.remaining_credits, key)
            entry = _Entry(bbox=key, fetched_at=self._clock(), time=resp.time, states=resp.states)
            with self._lock:
                self._entries[key] = entry
            self._prune()
            return self._result(entry, bbox, stale=False, credits=cost)

    def _stale_or_raise(self, bbox: BBox, reason: str, error: Exception | None = None):
        """Kapsayan eski bir kayıt varsa bayat olarak döner; yoksa hatayı fırlatır."""
        entry = self._find(bbox, fresh_only=False)
        if entry:
            log.warning("%s Bayat önbellek verisi döndürülüyor.", reason)
            return self._result(entry, bbox, stale=True, credits=0)
        raise error if error is not None else OpenSkyBudgetError(reason)
