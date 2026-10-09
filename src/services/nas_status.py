"""FAA NAS Status: ABD havalimanlarındaki güncel ground stop, ground delay programı,
genel kalkış/varış gecikmesi ve kapanışlar (anahtar gerekmez).

Gecikme modeli bu bilgiyi henüz özellik olarak kullanmaz (geçmiş arşivi yok); tahmin
yanıtında uyarı olarak gösterilir. NasStatusCache, durum her değiştiğinde ham yanıtı
nas_status_snapshots tablosuna yazar: veri birikince modele özellik olarak eklenebilir.
"""
import logging
import re
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests

log = logging.getLogger(__name__)

NAS_URL = "https://nasstatus.faa.gov/api/airport-status-information"
USER_AGENT = "GOKRADAR/1.0 (flight delay risk)"

# Yalnızca bir kısım trafiği kapatan NOTAM'lar ("tarifesiz GA'ya kapalı" gibi) yolcu
# uçuşlarını etkilemez; tam kapanış sayılmaz.
PARTIAL_CLOSURE_RE = re.compile(r"CLSD TO\b|NON SKED|TRANSIENT|WINGSPAN|\bGA\b", re.I)

EVENT_LABELS = {
    "ground_stop": "Ground stop (kalkışlar durduruldu)",
    "ground_delay": "Ground delay programı",
    "departure_delay": "Kalkış gecikmesi",
    "arrival_delay": "Varış gecikmesi",
    "closure": "Havalimanı kapalı",
}


class NasStatusError(RuntimeError):
    pass


def _text(el: ET.Element, tag: str) -> str:
    child = el.find(tag)
    return (child.text or "").strip() if child is not None and child.text else ""


def parse(xml_text: str) -> dict[str, list[dict]]:
    """XML → {havalimanı (FAA/IATA kodu): [olay, ...]}. Olay: {type, reason, detail}."""
    root = ET.fromstring(xml_text)
    events: dict[str, list[dict]] = {}

    def add(airport: str, kind: str, reason: str, detail: str = "") -> None:
        if airport:
            events.setdefault(airport.upper(), []).append(
                {"type": kind, "label": EVENT_LABELS[kind], "reason": reason, "detail": detail})

    for prog in root.iter("Program"):                       # Ground_Stop_List
        end = _text(prog, "End_Time")
        add(_text(prog, "ARPT"), "ground_stop", _text(prog, "Reason"), f"bitiş {end}" if end else "")
    for gd in root.iter("Ground_Delay"):                    # Ground_Delay_List
        avg, mx = _text(gd, "Avg"), _text(gd, "Max")
        detail = ", ".join(x for x in (f"ort. {avg}" if avg else "", f"en çok {mx}" if mx else "") if x)
        add(_text(gd, "ARPT"), "ground_delay", _text(gd, "Reason"), detail)
    for delay in root.iter("Delay"):                        # Arrival_Departure_Delay_List
        for ad in delay.findall("Arrival_Departure"):
            kind = "departure_delay" if ad.get("Type", "").lower().startswith("dep") else "arrival_delay"
            lo, hi, trend = _text(ad, "Min"), _text(ad, "Max"), _text(ad, "Trend")
            detail = " – ".join(x for x in (lo, hi) if x) + (f" ({trend})" if trend else "")
            add(_text(delay, "ARPT"), kind, _text(delay, "Reason"), detail)
    for ap in root.iter("Airport"):                         # Airport_Closure_List
        reason = _text(ap, "Reason")
        if not PARTIAL_CLOSURE_RE.search(reason):
            add(_text(ap, "ARPT"), "closure", reason, f"açılış {_text(ap, 'Reopen')}")
    return events


def fetch(timeout_s: float = 15.0) -> tuple[str, dict[str, list[dict]]]:
    try:
        resp = requests.get(NAS_URL, headers={"User-Agent": USER_AGENT, "Accept": "application/xml"},
                            timeout=timeout_s)
    except requests.RequestException as exc:
        raise NasStatusError(f"nasstatus.faa.gov erişilemedi: {exc}") from exc
    if resp.status_code != 200 or not resp.text.lstrip().startswith("<"):
        raise NasStatusError(f"nasstatus.faa.gov: HTTP {resp.status_code}")
    try:
        return resp.text, parse(resp.text)
    except ET.ParseError as exc:
        raise NasStatusError(f"NAS yanıtı çözümlenemedi: {exc}") from exc


class NasStatusCache:
    """Arka planda her poll_s saniyede bir yenilenir; tüm istekler aynı anlık görüntüyü okur.
    repo verilirse durum değiştikçe ham XML ile birlikte kaydedilir (gelecekteki eğitim verisi)."""

    def __init__(self, poll_s: int, repo=None, max_age_s: int | None = None):
        self.poll_s = poll_s
        self.max_age_s = max_age_s or poll_s * 3
        self.repo = repo
        self._events: dict[str, list[dict]] = {}
        self._fetched_at: float | None = None
        self._saved_events: dict | None = None
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, daemon=True, name="nas-status")
        self._thread.start()

    def _run(self) -> None:
        while True:
            try:
                raw, events = fetch()
                with self._lock:
                    self._events, self._fetched_at = events, time.time()
                # Yalnızca durum değiştiğinde kaydedilir (Neon ücretsiz katmanında yer kısıtlı);
                # aradaki süre boyunca durumun aynı kaldığı fetched_at farkından anlaşılır.
                if self.repo is not None and events != self._saved_events:
                    self._saved_events = events
                    try:
                        self.repo.save_nas_snapshot(fetched_at=datetime.now(timezone.utc),
                                                    events=events, raw_xml=raw)
                    except Exception as exc:  # noqa: BLE001 — kayıt hatası yenilemeyi durdurmasın
                        log.warning("NAS anlık görüntüsü kaydedilemedi: %s", exc)
            except NasStatusError as exc:
                log.warning("NAS durumu alınamadı: %s", exc)
            time.sleep(self.poll_s)

    def get(self, airport: str) -> list[dict] | None:
        """Havalimanının güncel olayları ([] = olay yok). Veri yoksa ya da eskiyse None."""
        with self._lock:
            if self._fetched_at is None or time.time() - self._fetched_at > self.max_age_s:
                return None
            return list(self._events.get(airport.upper(), []))
