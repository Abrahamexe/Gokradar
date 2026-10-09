"""Proje genelindeki ayarlar. Gizli değerler .env dosyasından okunur."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# --- Gizli değerler (.env) ---
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
DATABASE_URL_POOLED = os.getenv("DATABASE_URL_POOLED", "").strip()

_creds = os.getenv("OPENSKY_CREDENTIALS_PATH", "").strip()
OPENSKY_CREDENTIALS_PATH = (BASE_DIR / _creds) if _creds else None

# --- Klasörler ---
DATA_DIR = BASE_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
BTS_DIR = RAW_DIR / "bts"
WEATHER_DIR = RAW_DIR / "weather"
AIRCRAFT_DIR = RAW_DIR / "aircraft"
FGVC_DIR = RAW_DIR / "fgvc"
COMMONS_DIR = RAW_DIR / "commons"   # Wikimedia Commons'tan toplanan güncel tip fotoğrafları
PROCESSED_DIR = DATA_DIR / "processed"
UPLOADS_DIR = DATA_DIR / "uploads"
MODELS_DIR = BASE_DIR / "models"

# --- Tahmin kapsamı: ABD'nin en yoğun havalimanları (IATA) ---
TARGET_AIRPORTS = ["ATL", "DFW", "DEN", "ORD", "LAX", "JFK", "LAS", "CLT", "MCO", "SEA"]

# Havalimanı saat dilimleri — BTS saatleri yerel, METAR saatleri UTC.
# TARGET_AIRPORTS'a eklenen her havalimanı burada da olmalı.
AIRPORT_TZ = {
    "ATL": "America/New_York", "JFK": "America/New_York", "CLT": "America/New_York",
    "MCO": "America/New_York", "ORD": "America/Chicago", "DFW": "America/Chicago",
    "DEN": "America/Denver", "LAX": "America/Los_Angeles", "LAS": "America/Los_Angeles",
    "SEA": "America/Los_Angeles",
}

# Gecikme eşiği (dakika) — BTS DEP_DEL15 tanımıyla uyumlu
DELAY_THRESHOLD_MIN = 15

# --- Gecikme modeli ---
DELAY_TEST_DAYS = 14       # verinin son N günü test seti
DELAY_VAL_DAYS = 7         # testten önceki N gün: erken durdurma ve eşik seçimi
MIN_DEST_FLIGHTS = 100     # eğitimde bundan az uçuşu olan varış havalimanları → "OTHER"
DELAY_MODEL_DIR = MODELS_DIR / "delay"

# --- Arayüz ---
APP_NAME = "GÖKRADAR"             # geçici proje adı
AIRPORT_NAMES = {
    "ATL": "Atlanta", "DFW": "Dallas/Fort Worth", "DEN": "Denver", "ORD": "Chicago O'Hare",
    "LAX": "Los Angeles", "JFK": "New York JFK", "LAS": "Las Vegas", "CLT": "Charlotte",
    "MCO": "Orlando", "SEA": "Seattle",
}
# Harita karo katmanı (koyu tema). Stadia Maps ücretsiz planı ticari olmayan kullanım içindir;
# localhost'ta anahtarsız çalışır, gerçek bir alan adında ücretsiz hesap + STADIA_API_KEY gerekir.
STADIA_API_KEY = os.getenv("STADIA_API_KEY", "").strip()
MAP_TILES = {
    "name": "Stadia Alidade Smooth Dark",
    "url": "https://tiles.stadiamaps.com/tiles/alidade_smooth_dark/{z}/{x}/{y}{r}.png"
           + (f"?api_key={STADIA_API_KEY}" if STADIA_API_KEY else ""),
    "attribution": '&copy; <a href="https://stadiamaps.com/">Stadia Maps</a> '
                   '&copy; <a href="https://openmaptiles.org/">OpenMapTiles</a> '
                   '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> katkıcıları',
    "max_zoom": 20,
}
# Fotoğrafta birbirine çok benzeyen aileler (test setinde en çok karıştırılan çiftler);
# sonuçta tek satırda birleşik olasılıkla gösterilir.
CONFUSABLE_FAMILIES = [
    ["DC-3", "C-47"],
    ["DC-9", "MD-80", "MD-90", "Boeing 717"],   # DC-9 gövdesinin nesilleri
    ["A300", "A310"],
    ["ATR-42", "ATR-72"],
    ["DC-10", "MD-11"],
]

# --- REST API ---
MAX_UPLOAD_MB = 10                  # classify-aircraft yükleme sınırı
ALLOWED_IMAGE_FORMATS = {"JPEG": {".jpg", ".jpeg"}, "PNG": {".png"}}
METAR_CACHE_TTL_S = 300             # aynı istasyonun METAR'ı en fazla 5 dk'da bir çekilir
NAS_POLL_S = 300                    # FAA NAS Status yenileme aralığı (durum değişince DB'ye kaydedilir)

# İstemci (IP) başına dakikadaki en fazla istek. Yazma yapan ve dış servislere giden endpoint'ler.
RATE_LIMITS_PER_MIN = {"predict": 30, "classify": 10, "route": 90, "aircraft": 120}
# Uygulama bir ters vekilin (nginx, Cloudflare...) arkasındaysa vekil sayısı; IP başına sınır ve
# loglar gerçek istemci adresini X-Forwarded-For'dan okusun diye. Doğrudan erişimde 0 bırakın:
# 0 değilken vekilsiz erişimde başlık sahtelenebilir ve hız sınırı atlatılır.
TRUSTED_PROXY_HOPS = int(os.getenv("TRUSTED_PROXY_HOPS", "0") or 0)

# OpenSky: aynı bölge için en fazla TTL'de bir istek; tüm kullanıcılar önbellekten beslenir.
OPENSKY_CACHE_TTL_S = 60
OPENSKY_GRID_DEG = 0.5              # istenen bölge bu ızgaraya dışa doğru yuvarlanır (önbellek paylaşımı)
OPENSKY_MAX_AREA_SQDEG = 400        # bundan büyük bölgeler reddedilir (en pahalı kademe)
OPENSKY_DAILY_CREDITS = 4000        # standart kullanıcı günlük kredi hakkı (UTC gün)
OPENSKY_CREDIT_RESERVE = 100        # kalan kredi bunun altına inerse yeni istek atılmaz, eski veri döner

# adsb.lol: haritanın ana canlı veri kaynağı (dünya geneli, askeri uçaklar dahil, anahtarsız).
# Tüm kullanıcılar sunucudaki tek anlık görüntüyü paylaşır; adsb.lol'e en fazla bu aralıkta istek gider.
ADSB_API_URL = "https://api.adsb.lol"
ADSB_WORLD_RADIUS_NM = 10800        # (0°, 0°) merkezli bu yarıçap tüm dünyayı kapsar
ADSB_POLL_S = 10
# Dünya verisi askeri uçakları zaten içerir; adsb.lol/v2/mil aynı sunucuya ek yük (429) bindirdiği için yalnızca adsb.fi
ADSB_MIL_URLS = ["https://opendata.adsb.fi/api/v2/mil"]
ADSB_MIL_POLL_S = 30                # ek askeri akışlar bu aralıkta yenilenir
MAP_POLL_S = 5                      # tarayıcının sunucuya sorma aralığı (yeni veri yoksa 304 döner)
ROUTE_API_URL = "https://api.adsbdb.com"   # çağrı kodu → kalkış/varış havalimanı (tarife verisi)
