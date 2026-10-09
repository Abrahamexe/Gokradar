"""API'nin kullandığı servisler — uygulama açılışında bir kez oluşturulur.

Testlerde aynı arayüze sahip sahte nesneler verilebilir.
"""
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path

import config

log = logging.getLogger(__name__)


class AircraftLookup:
    """ICAO24 → uçak bilgisi; statik veri olduğu için bellekte tutulur (bulunamayanlar dahil)."""

    def __init__(self, repo, max_size: int = 50_000):
        self.repo = repo
        self.max_size = max_size
        self._cache: dict[str, dict | None] = {}
        self._lock = threading.Lock()
        self._airlines: dict[str, str] | None = None
        self._airlines_lock = threading.Lock()

    def get_many(self, icao24s: list[str]) -> dict[str, dict | None]:
        with self._lock:
            missing = [i for i in set(icao24s) if i not in self._cache]
        if missing:
            found = self.repo.get_aircraft_many(missing)
            with self._lock:
                if len(self._cache) + len(missing) > self.max_size:
                    self._cache.clear()
                self._cache.update({i: found.get(i) for i in missing})
        with self._lock:
            return {i: self._cache.get(i) for i in icao24s}

    def airline_name(self, callsign: str | None) -> str | None:
        """Çağrı kodunun ICAO öneki (THY123 → THY) için havayolu adı; tablo ilk çağrıda bir kez yüklenir."""
        if not callsign or len(callsign) < 4 or not callsign[:3].isalpha() or not callsign[3].isdigit():
            return None
        airlines = self.load_airlines()
        return airlines.get(callsign[:3].upper()) if airlines else None

    def load_airlines(self) -> dict[str, str] | None:
        with self._airlines_lock:
            if self._airlines is None:
                try:
                    self._airlines = self.repo.get_airline_names()
                except Exception as exc:  # noqa: BLE001 — bir sonraki çağrıda tekrar denenir
                    log.warning("Havayolu adları yüklenemedi: %s", exc)
            return self._airlines


@dataclass
class Services:
    delay_predictor: object
    classifier: object
    repo: object
    metar: object                      # .get(station) -> dict | None
    opensky: object | None             # OpenSkyCache; kimlik yoksa None
    adsb: object | None = None         # AdsbFeed: haritanın dünya geneli canlı verisi
    routes: object | None = None       # RouteLookup: çağrı kodu → kalkış/varış
    nas: object | None = None          # NasStatusCache: FAA ground stop / gecikme programları
    upload_dir: Path = config.UPLOADS_DIR
    aircraft_lookup: AircraftLookup = field(init=False)
    classifier_lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self):
        self.aircraft_lookup = AircraftLookup(self.repo)


def build_services() -> Services:
    """Gerçek servisler: modeller bir kez yüklenir, DB için pooler bağlantısı kullanılır."""
    from src.db.connection import get_engine
    from src.db.repository import Repository
    from src.ml.delay.predict import DelayPredictor
    from src.ml.vision.predict import AircraftClassifier
    from src.services.adsb_feed import AdsbFeed
    from src.services.metar_client import MetarCache
    from src.services.nas_status import NasStatusCache
    from src.services.route_lookup import RouteLookup
    from src.services.opensky_cache import OpenSkyCache
    from src.services.opensky_client import OpenSkyClient, OpenSkyError

    delay = DelayPredictor()
    classifier = AircraftClassifier()
    log.info("Modeller yüklendi: gecikme=%s, görüntü=%s (%s)", delay.version, classifier.version,
             classifier.device)
    try:
        opensky = OpenSkyCache(OpenSkyClient())
    except OpenSkyError as exc:
        log.warning("OpenSky devre dışı: %s", exc)
        opensky = None
    repo = Repository(get_engine(pooled=True))
    svc = Services(delay_predictor=delay, classifier=classifier, repo=repo,
                   metar=MetarCache(config.METAR_CACHE_TTL_S), opensky=opensky, adsb=AdsbFeed(),
                   routes=RouteLookup(), nas=NasStatusCache(config.NAS_POLL_S, repo=repo))
    # Havayolu adları tablosu (~5 sn) ilk uçak seçimini bekletmesin
    threading.Thread(target=svc.aircraft_lookup.load_airlines, daemon=True, name="airlines").start()
    return svc
