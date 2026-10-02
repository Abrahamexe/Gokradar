"""İstemci (IP) başına kayan pencereli hız sınırı — bağımlılıksız, bellekte.

Yazma yapan (veritabanı/disk) ve dış servislere giden endpoint'leri kötüye kullanıma karşı korur.
Birden çok süreç çalıştırılırsa sayaç süreç başınadır; tek süreçli kurulum (waitress) için yeterlidir.
"""
import functools
import threading
import time
from collections import deque

from flask import current_app, request

from src.api.validation import ApiError

MAX_TRACKED_KEYS = 20_000   # bellek tavanı: aşılırsa süresi dolmuş kayıtlar temizlenir


class RateLimiter:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._hits: dict[tuple[str, str], deque] = {}
        self._lock = threading.Lock()

    def check(self, bucket: str, client: str, limit: int, window_s: float) -> float | None:
        """İstek sayılır. Sınır aşıldıysa beklenecek süreyi (sn), aksi halde None döner."""
        now = self._clock()
        key = (bucket, client)
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and now - q[0] >= window_s:
                q.popleft()
            if len(q) >= limit:
                return max(1.0, window_s - (now - q[0]))
            q.append(now)
            if len(self._hits) > MAX_TRACKED_KEYS:
                self._sweep(now, window_s)
            return None

    def _sweep(self, now: float, window_s: float) -> None:
        for k in [k for k, q in self._hits.items() if not q or now - q[-1] >= window_s]:
            del self._hits[k]


def rate_limited(bucket: str, limit: int, window_s: float = 60.0):
    """Route dekoratörü. Sınırlayıcı app.extensions["rate_limiter"] içindedir; yoksa/kapalıysa geçer."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            limiter = current_app.extensions.get("rate_limiter")
            if limiter is not None:
                wait = limiter.check(bucket, request.remote_addr or "?", limit, window_s)
                if wait is not None:
                    err = ApiError(429, "Çok fazla istek; kısa süre sonra tekrar deneyin.", "rate_limited")
                    err.retry_after = int(wait) + 1
                    raise err
            return fn(*args, **kwargs)
        return wrapper
    return deco
