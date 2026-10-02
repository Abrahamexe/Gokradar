"""OpenSky Network REST API istemcisi (OAuth2 client credentials).

- Erişim tokenı önbelleğe alınır, süresi dolmadan kısa süre önce yenilenir.
- 401 yanıtında token bir kez yenilenip istek tekrarlanır.
- 429 (rate limit) yanıtı OpenSkyRateLimitError olarak, bekleme süresiyle birlikte fırlatılır.
"""
import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import requests

import config

TOKEN_URL = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"
API_URL = "https://opensky-network.org/api"
TOKEN_REFRESH_MARGIN_S = 60

# /states/all yanıtındaki durum vektörü alanları (extended=1 ile "category" eklenir)
STATE_FIELDS = [
    "icao24", "callsign", "origin_country", "time_position", "last_contact",
    "longitude", "latitude", "baro_altitude", "on_ground", "velocity",
    "true_track", "vertical_rate", "sensors", "geo_altitude", "squawk",
    "spi", "position_source", "category",
]


class OpenSkyError(RuntimeError):
    pass


class OpenSkyRateLimitError(OpenSkyError):
    def __init__(self, retry_after_s: int | None):
        self.retry_after_s = retry_after_s
        wait = f"{retry_after_s} sn sonra" if retry_after_s is not None else "daha sonra"
        super().__init__(f"OpenSky rate limit aşıldı; {wait} tekrar deneyin.")


@dataclass
class StatesResponse:
    time: int
    states: list[dict]
    remaining_credits: int | None


class OpenSkyClient:
    def __init__(self, credentials_path: Path | None = None, timeout_s: float = 20.0):
        path = credentials_path or config.OPENSKY_CREDENTIALS_PATH
        if not path or not Path(path).exists():
            raise OpenSkyError(f"OpenSky kimlik dosyası bulunamadı: {path}")
        creds = json.loads(Path(path).read_text(encoding="utf-8"))
        self._client_id = creds["clientId"]
        self._client_secret = creds["clientSecret"]
        self._timeout = timeout_s
        self._session = requests.Session()
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._lock = threading.Lock()

    # --- ağ ---
    def _send(self, send, *args, **kwargs) -> requests.Response:
        """Ağ hatalarını OpenSkyError'a çevirir. Sunucunun boşta kapattığı keep-alive bağlantısı
        (RemoteDisconnected vb.) bir kez yeni bağlantıyla tekrar denenir."""
        for attempt in range(2):
            try:
                return send(*args, timeout=self._timeout, **kwargs)
            except requests.Timeout as exc:
                raise OpenSkyError("OpenSky zamanında yanıt vermedi.") from exc
            except requests.ConnectionError as exc:
                if attempt == 0:
                    continue
                raise OpenSkyError(f"OpenSky'a bağlanılamadı: {exc}") from exc
            except requests.RequestException as exc:
                raise OpenSkyError(f"OpenSky isteği başarısız: {exc}") from exc

    # --- token ---
    def _fetch_token(self) -> None:
        resp = self._send(self._session.post, TOKEN_URL, data={
            "grant_type": "client_credentials",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
        })
        if resp.status_code != 200:
            raise OpenSkyError(f"Token alınamadı: HTTP {resp.status_code} {resp.text[:200]}")
        body = resp.json()
        self._token = body["access_token"]
        self._token_expires_at = time.monotonic() + int(body.get("expires_in", 1800))

    def _get_token(self, force: bool = False) -> str:
        with self._lock:
            if force or self._token is None or \
                    time.monotonic() >= self._token_expires_at - TOKEN_REFRESH_MARGIN_S:
                self._fetch_token()
            return self._token

    # --- istekler ---
    def _get(self, path: str, params: dict | None = None) -> requests.Response:
        for attempt in range(2):
            headers = {"Authorization": f"Bearer {self._get_token(force=attempt > 0)}"}
            resp = self._send(self._session.get, f"{API_URL}{path}", params=params, headers=headers)
            if resp.status_code == 401 and attempt == 0:
                continue  # token geçersiz: yenile ve bir kez daha dene
            break
        if resp.status_code == 429:
            retry = resp.headers.get("X-Rate-Limit-Retry-After-Seconds")
            raise OpenSkyRateLimitError(int(retry) if retry and retry.isdigit() else None)
        if resp.status_code != 200:
            raise OpenSkyError(f"OpenSky {path}: HTTP {resp.status_code} {resp.text[:200]}")
        return resp

    def get_states(self, lamin: float | None = None, lomin: float | None = None,
                   lamax: float | None = None, lomax: float | None = None) -> StatesResponse:
        """Canlı durum vektörleri. Sınır kutusu verilirse yalnızca o bölge döner
        (küçük bölgeler daha az API kredisi harcar)."""
        bbox = [lamin, lomin, lamax, lomax]
        params = {"extended": 1}
        if any(v is not None for v in bbox):
            if any(v is None for v in bbox):
                raise ValueError("Sınır kutusu için lamin, lomin, lamax, lomax birlikte verilmeli.")
            params.update(lamin=lamin, lomin=lomin, lamax=lamax, lomax=lomax)

        resp = self._get("/states/all", params)
        body = resp.json()
        states = [dict(zip(STATE_FIELDS, row)) for row in (body.get("states") or [])]
        for s in states:
            s["icao24"] = (s["icao24"] or "").strip().lower()
            s["callsign"] = (s["callsign"] or "").strip() or None
        remaining = resp.headers.get("X-Rate-Limit-Remaining")
        return StatesResponse(time=body.get("time"), states=states,
                              remaining_credits=int(remaining) if remaining and remaining.isdigit() else None)
