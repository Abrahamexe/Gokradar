"""adsb.lol dünya geneli canlı uçak verisi (ücretsiz, anahtarsız, filtresiz ADS-B + MLAT ağı).

OpenSky'dan farkı: askeri uçaklar dahil filtrelenmez, alıcı ağı daha geniştir ve tek istekte tüm
dünya gelir (kredi sınırı yok). Haritanın ana veri kaynağı budur.

- Tüm kullanıcılar aynı anlık görüntüyü paylaşır; en fazla `interval_s`'de bir istek atılır.
- Görüntü eskiyince isteği tetikleyen kullanıcı beklemez: yenileme arka planda yapılır,
  o sırada eldeki görüntü döner (yalnızca ilk istek bekler).
- Yanıt bir kez sıkıştırılıp (gzip) önbelleğe alınır; her istemci için yeniden üretilmez.
"""
import gzip
import json
import logging
import threading
import time
from dataclasses import dataclass, field

import requests

import config

log = logging.getLogger(__name__)

FRESH_MIN_AGE_S = 3.0   # fresh=True isteği, bundan taze bir görüntü için kaynağa gitmez

# Haritanın kullandığı sıkıştırılmış satır biçimi (alan adları yanıtta "fields" olarak da gelir)
FIELDS = ["icao24", "callsign", "registration", "typecode", "lat", "lon", "alt_ft", "gs_kt",
          "track", "vrate_fpm", "kind", "seen_s", "squawk"]

# kind: haritada renk/simge ve filtre grubu
KIND_AIRLINER, KIND_CARGO, KIND_MILITARY, KIND_GA, KIND_HELI, KIND_GROUND, KIND_FIGHTER = range(7)

# Savaş ve jet eğitim uçakları (ICAO tip kodları). Operasyondaki savaş uçakları çoğunlukla
# transponder yayını yapmaz; görünenler genelde eğitim/transit uçuşlarıdır.
FIGHTER_TYPES = {
    "F16", "F15", "F15E", "F18", "F18S", "F18H", "F35", "F22", "F14", "F5", "F4", "A10", "AV8",
    "EUFI", "RFAL", "GRIF", "TOR", "MIR2", "M2000", "MG29", "SU24", "SU25", "SU27", "SU30", "SU34",
    "SU35", "J10", "JF17", "KF21", "HURJ", "T38", "T7", "HAWK", "M346", "L39", "L159", "AJET",
    "K8", "T50", "HJT36", "PC21", "TUCA", "T6", "TEX2", "PC9", "PC7", "HUR", "B1", "B2", "B52",
}

# Kargo havayollarının ICAO çağrı kodu önekleri
CARGO_PREFIXES = {
    "FDX", "UPS", "GTI", "CLX", "CKS", "ABW", "BOX", "GEC", "CAO", "CKK", "ABX", "ATN", "NCA",
    "AHK", "DHK", "DHX", "BCS", "SQC", "MPH", "ICV", "TAY", "SWN", "MSX", "ADB", "TPA", "LCO",
    "MNB", "NPT", "KFS", "PAC", "SRR", "GSS",
}
# Yer araçları (C1 acil durum, C2 servis aracı) ve sabit engeller uçak değildir
SKIP_CATEGORIES = {"C1", "C2", "C3", "C4", "C5"}
GA_CATEGORIES = {"A1", "B1", "B2", "B3", "B4", "B6"}


class AdsbFeedError(RuntimeError):
    def __init__(self, message: str, retry_after_s: float | None = None):
        super().__init__(message)
        self.retry_after_s = retry_after_s   # sunucu "çok fazla istek" dediyse beklenecek süre


def clean_callsign(value: str | None) -> str | None:
    """Alıcıların gönderdiği bozuk çağrı kodlarını ('@@@@@@@@', '.N196UW') temizler."""
    cs = (value or "").replace("@", "").strip(" .-_")
    return cs if cs and cs.strip("0") else None   # boş ya da yalnızca sıfır ("00000000") çağrı kodu yok sayılır


def classify(ac: dict) -> int:
    """Uçağın harita grubunu belirler (askeri > helikopter > kargo > genel havacılık > yolcu)."""
    if ac.get("alt_baro") == "ground":
        return KIND_GROUND
    if (ac.get("t") or "").upper() in FIGHTER_TYPES:
        return KIND_FIGHTER
    if (ac.get("dbFlags") or 0) & 1:
        return KIND_MILITARY
    cat = ac.get("category")
    if cat == "A7":
        return KIND_HELI
    cs = (clean_callsign(ac.get("flight")) or "").upper()
    if cs[:3] in CARGO_PREFIXES:
        return KIND_CARGO
    if cat in GA_CATEGORIES:
        return KIND_GA
    return KIND_AIRLINER


def _num(v, digits=None):
    if not isinstance(v, (int, float)):
        return None
    return round(v, digits) if digits is not None else round(v)


def compact(ac: dict) -> list | None:
    """adsb.lol uçak kaydı → FIELDS sırasında liste. Konumsuz kayıtlar ve yer araçları atlanır."""
    lat, lon = ac.get("lat"), ac.get("lon")
    if lat is None or lon is None or ac.get("category") in SKIP_CATEGORIES:
        return None
    alt = ac.get("alt_baro")
    if not isinstance(alt, (int, float)):
        alt = ac.get("alt_geom") if alt != "ground" else 0
    track = ac.get("track")
    if track is None:
        track = ac.get("true_heading", ac.get("mag_heading"))
    vrate = ac.get("baro_rate", ac.get("geom_rate"))
    return [
        (ac.get("hex") or "").lower().lstrip("~"),
        clean_callsign(ac.get("flight")),
        ac.get("r") or None,
        ac.get("t") or None,
        round(lat, 4), round(lon, 4),
        _num(alt), _num(ac.get("gs")), _num(track, 1), _num(vrate),
        classify(ac),
        _num(ac.get("seen_pos", ac.get("seen")), 1),
        ac.get("squawk") or None,
    ]


def fetch_military(session: requests.Session, timeout_s: float = 20.0) -> list[dict]:
    """Ek askeri akışlar (adsb.fi, adsb.lol /v2/mil): dünya sorgusunda eksik kalan askeri uçaklar.
    Hata veren kaynak atlanır."""
    out = []
    for url in config.ADSB_MIL_URLS:
        try:
            resp = session.get(url, timeout=timeout_s, headers={"User-Agent": "gokradar/1.0"})
            if resp.status_code == 200:
                for ac in resp.json().get("ac") or []:
                    ac["dbFlags"] = (ac.get("dbFlags") or 0) | 1
                    out.append(ac)
            else:
                log.info("Askeri akış %s: HTTP %s", url, resp.status_code)
        except (requests.RequestException, ValueError) as exc:
            log.info("Askeri akış %s alınamadı: %s", url, exc)
    return out


def merge_extra(data: dict, extra: list[dict]) -> int:
    """Dünya verisinde olmayan ek uçakları ekler; eklenen sayısı."""
    ac = data.setdefault("ac", [])
    seen = {(a.get("hex") or "").lower().lstrip("~") for a in ac}
    added = 0
    for a in extra:
        key = (a.get("hex") or "").lower().lstrip("~")
        if key and key not in seen:
            seen.add(key)
            ac.append(a)
            added += 1
    return added


def fetch_world(session: requests.Session, timeout_s: float = 30.0) -> dict:
    """Dünyanın tamamı: (0°, 0°) merkezli, yarıçapı yer küresinin yarı çevresinden büyük nokta sorgusu."""
    url = f"{config.ADSB_API_URL}/v2/point/0/0/{config.ADSB_WORLD_RADIUS_NM}"
    try:
        resp = session.get(url, timeout=timeout_s, headers={"Accept-Encoding": "gzip"})
    except requests.RequestException as exc:
        raise AdsbFeedError(f"adsb.lol'e bağlanılamadı: {exc}") from exc
    if resp.status_code == 429:
        retry = resp.headers.get("Retry-After", "")
        raise AdsbFeedError("adsb.lol HTTP 429", retry_after_s=max(20.0, float(retry)) if retry.isdigit() else 20.0)
    if resp.status_code != 200:
        raise AdsbFeedError(f"adsb.lol HTTP {resp.status_code}")
    try:
        return resp.json()
    except ValueError as exc:
        raise AdsbFeedError("adsb.lol geçersiz yanıt döndürdü.") from exc


@dataclass
class Snapshot:
    time: float                       # verinin zamanı (unix sn)
    fetched_at: float                 # sunucunun aldığı an (unix sn)
    rows: list[list]
    raw: dict[str, dict]              # icao24 → adsb.lol kaydı (seçili uçak ayrıntısı için)
    body_gzip: bytes = field(repr=False, default=b"")
    etag: str = ""

    @property
    def count(self) -> int:
        return len(self.rows)


def build_snapshot(data: dict, fetched_at: float) -> Snapshot:
    rows, raw = [], {}
    for ac in data.get("ac") or []:
        row = compact(ac)
        if row and row[0]:
            rows.append(row)
            raw[row[0]] = ac
    t = (data.get("now") or fetched_at * 1000) / 1000
    body = json.dumps({"source": "adsb.lol", "time": t, "fetched_at": fetched_at, "count": len(rows),
                       "fields": FIELDS, "ac": rows}, ensure_ascii=False, separators=(",", ":"))
    return Snapshot(time=t, fetched_at=fetched_at, rows=rows, raw=raw,
                    body_gzip=gzip.compress(body.encode("utf-8"), compresslevel=5),
                    etag=f'"{int(t * 1000)}"')


class AdsbFeed:
    def __init__(self, fetch=None, interval_s: float = config.ADSB_POLL_S, clock=time.time,
                 fetch_extra=None, extra_interval_s: float = config.ADSB_MIL_POLL_S):
        self._session = requests.Session()
        self._fetch = fetch or (lambda: fetch_world(self._session))
        # Ek askeri akışlar dünya verisinden daha seyrek çekilir; testlerde varsayılan olarak kapalı
        self._fetch_extra = fetch_extra if fetch_extra is not None else (
            (lambda: fetch_military(self._session)) if fetch is None else None)
        self.extra_interval_s = extra_interval_s
        self._extra: list[dict] = []
        self._extra_at = -1e18
        self.interval_s = interval_s
        self._clock = clock
        self._snap: Snapshot | None = None
        self._lock = threading.Lock()          # tek seferde tek yenileme
        self._cond = threading.Condition()
        self.last_error: str | None = None
        self._last_attempt = 0.0
        self._wait_until = 0.0                 # 429 sonrası bu ana kadar istek atılmaz

    def _refresh(self) -> None:
        try:
            started = self._clock()
            data = self._fetch()
            if self._fetch_extra and self._clock() - self._extra_at >= self.extra_interval_s:
                self._extra_at = self._clock()
                self._extra = self._fetch_extra()
            if self._extra:
                merge_extra(data, self._extra)
            snap = build_snapshot(data, self._clock())
            self.last_error = None
            log.info("adsb.lol: %d uçak, %.1f sn", snap.count, self._clock() - started)
            with self._cond:
                self._snap = snap
        except Exception as exc:  # noqa: BLE001 — eldeki görüntü kullanılmaya devam eder
            self.last_error = str(exc)
            wait = getattr(exc, "retry_after_s", None)
            if wait:
                self._wait_until = self._clock() + wait
            log.warning("adsb.lol yenilenemedi: %s%s", exc, f" ({wait:.0f} sn beklenecek)" if wait else "")
        finally:
            self._lock.release()
            with self._cond:
                self._cond.notify_all()

    def _maybe_refresh(self) -> bool:
        """Görüntü eskiyse ve yenileme sürmüyorsa yenilemeyi başlatır. Başladıysa True."""
        now = self._clock()
        snap = self._snap
        due = snap is None or now - snap.fetched_at >= self.interval_s
        # Hata sonrası sunucuyu yormamak için en az bir aralık bekle
        if (not due or now < self._wait_until or now - self._last_attempt < min(self.interval_s, 5)
                or not self._lock.acquire(blocking=False)):
            return False
        self._last_attempt = now
        if snap is None:
            self._refresh()                    # ilk veri: çağıran bekler
        else:
            threading.Thread(target=self._refresh, daemon=True, name="adsb-refresh").start()
        return True

    def _refresh_now(self, wait_s: float) -> None:
        """İstemcinin açıkça istediği yenileme: eski görüntüyü hemen vermek yerine yenisini bekler.
        Görüntü çok tazeyse ya da 429 beklemesi sürüyorsa kaynağa gitmez; yenileme zaten sürüyorsa onu bekler."""
        now = self._clock()
        snap = self._snap
        if now < self._wait_until or (snap is not None and now - snap.fetched_at < FRESH_MIN_AGE_S):
            return
        if self._lock.acquire(blocking=False):
            self._last_attempt = now
            self._refresh()                    # kilidi kendisi bırakır
        else:
            with self._cond:
                self._cond.wait_for(lambda: not self._lock.locked(), timeout=wait_s)

    def snapshot(self, wait_s: float = 35.0, fresh: bool = False) -> Snapshot:
        """Güncel görüntü. Hiç veri yoksa ilk yenilemeyi bekler; o da başarısızsa AdsbFeedError.
        fresh=True ise görüntü eskiyse yenisi alınana kadar beklenir (radardaki yenile düğmesi)."""
        if fresh and self._snap is not None:
            self._refresh_now(wait_s)
        else:
            self._maybe_refresh()
        if self._snap is None:
            with self._cond:
                self._cond.wait_for(lambda: self._snap is not None or not self._lock.locked(), timeout=wait_s)
        if self._snap is None:
            raise AdsbFeedError(self.last_error or "Canlı uçak verisi henüz alınamadı.")
        return self._snap
