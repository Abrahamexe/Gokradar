<div align="center">

# ✈️ GÖKRADAR

**Gökyüzündeki uçakları canlı izleyin, kalkışların gecikme riskini öngörün, bir fotoğraftan uçağın tipini tanıyın.**

![Python](https://img.shields.io/badge/Python-3.14-3776AB?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.x-000000?logo=flask&logoColor=white)
![LightGBM](https://img.shields.io/badge/LightGBM-gecikme%20modeli-2E8B57)
![PyTorch](https://img.shields.io/badge/PyTorch-EfficientNet--B0-EE4C2C?logo=pytorch&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-Neon-4169E1?logo=postgresql&logoColor=white)
![Lisans](https://img.shields.io/badge/lisans-özel%20(tüm%20hakları%20saklıdır)-lightgrey)

<img src="docs/images/anasayfa.jpg" alt="GÖKRADAR ana sayfa: teknik çizim olarak beliren Boeing 787" width="860">

</div>

---

## Neler yapar?

| | Modül | Ne sunar |
|---|---|---|
| 🛰️ | **Canlı harita** | Dünyadaki ~11 000 uçağı gerçek zamanlı gösterir (yolcu, kargo, askeri, savaş jeti, helikopter, genel havacılık). Uçak seçilince tescil, tip, operatör, rota ve irtifa geçmişi görünür. |
| ⏱️ | **Gecikme tahmini** | Kalkış, varış, havayolu, tarih ve saat girilir; kalkış havalimanının **güncel METAR** hava durumu da hesaba katılarak uçuşun **15 dakikadan fazla gecikme olasılığı** döner. Aynı rota için günün farklı saatleri de karşılaştırılır. |
| 📷 | **Uçak tanıma** | Yüklenen fotoğraftan 70 uçak ailesi arasında en olası üç tahmin ve güven skorları. Birbirine çok benzeyen aileler (DC-3 / C-47 gibi) tek satırda birleştirilir. |

## Ekran görüntüleri

<table>
  <tr>
    <td width="50%"><img src="docs/images/harita.jpg" alt="Canlı harita"><br><sub><b>Canlı harita</b> — adsb.lol verisiyle dünya geneli</sub></td>
    <td width="50%"><img src="docs/images/gecikme.jpg" alt="Gecikme tahmini"><br><sub><b>Gecikme tahmini</b> — olasılık, günün saatleri, güncel METAR</sub></td>
  </tr>
  <tr>
    <td colspan="2"><img src="docs/images/tanima.jpg" alt="Uçak tanıma"><br><sub><b>Uçak tanıma</b> — fotoğraf yükle, en olası üç aileyi gör</sub></td>
  </tr>
</table>

## Nasıl çalışır?

```
 Tarayıcı ──────────────► Flask API (waitress) ───────────────────────────────────────┐
  harita / form / yükleme    │  hız sınırı · CSP · önbellek · gzip                      │
                             ├─► DelayPredictor   LightGBM + kalkış METAR'ı             │
                             ├─► AircraftClassifier   EfficientNet-B0 (CUDA / CPU)      │
                             ├─► AdsbFeed         adsb.lol (dünya) + adsb.fi (askeri)   │
                             ├─► OpenSkyCache     bölge önbelleği + günlük kredi sayacı │
                             ├─► MetarCache · RouteLookup (adsbdb.com)                  │
                             └─► Repository ──► Neon PostgreSQL (uçak DB, kayıtlar) ◄───┘
```

- **Tek paylaşımlı anlık görüntü:** tüm kullanıcılar sunucudaki aynı canlı veri görüntüsünü alır; dış servise
  en fazla her 10 saniyede bir istek gider. Yanıt bir kez gzip'lenir, `ETag` ile değişmediyse `304` döner.
- **Modeller açılışta bir kez yüklenir**, her istekte değil. Gecikme tahmininde günün birden çok saati tek
  model çağrısıyla hesaplanır.
- **Gecikme modelinde veri sızıntısı yok:** yalnızca kalkıştan önce bilinen özellikler kullanılır
  (zaman, havayolu, rota, mesafe, METAR, havalimanı yoğunluğu).

## Model sonuçları

**Gecikme modeli** — LightGBM, ABD'nin en yoğun 10 havalimanı (ATL, DFW, DEN, ORD, LAX, JFK, LAS, CLT, MCO, SEA),
BTS verisinin Haziran–Temmuz 2026 dönemi; son 14 gün test seti (90 383 uçuş, gecikme oranı %32,4).

| Model | ROC-AUC | PR-AUC | F1 | Recall | Brier |
|---|---|---|---|---|---|
| Lojistik regresyon (eşik ayarlı) | 0,696 | 0,517 | 0,547 | 0,728 | 0,197 |
| **LightGBM (eşik ayarlı)** ★ | **0,712** | **0,536** | **0,559** | **0,719** | **0,193** |

> Gecikme, havalimanı ve hava durumu gibi çoğu zaman kontrol dışı etkenlere bağlı olduğundan bu bir **risk
> göstergesidir, garanti değil**. Model olasılığı sistematik olarak biraz düşük tahmin eder (test dönemi
> eğitim döneminden daha gecikmeliydi); kalibrasyon grafiği eğitim çıktılarında bulunur.

**Uçak sınıflandırıcı** — EfficientNet-B0 (transfer learning), FGVC-Aircraft, 70 aile, 3 333 test görüntüsü.

| Metrik | Test |
|---|---|
| Top-1 doğruluk | **%89,1** |
| Top-3 doğruluk | **%96,0** |
| Macro doğruluk | %88,8 |

Veri seti 2013 tarihli olduğundan A350 veya 737 MAX gibi yeni tipler en benzer aileye atanır.

## Teknolojiler

| Katman | Kullanılanlar |
|---|---|
| Arka uç | Python 3.14, Flask, waitress, Flask-Compress, SQLAlchemy |
| Veritabanı | PostgreSQL (Neon, pooler bağlantısı) |
| Makine öğrenmesi | LightGBM, scikit-learn, pandas, PyTorch, torchvision, Pillow |
| Ön uç | Düz JavaScript, Leaflet (harita), three.js + GLTF (3B ana sayfa), Lenis |
| Veri | adsb.lol, adsb.fi, OpenSky Network, adsbdb.com, BTS, aviationweather.gov, Iowa Environmental Mesonet |

## Kurulum

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env                          # değerleri doldurun
copy credentials.example.json credentials.json  # OpenSky kullanacaksanız (isteğe bağlı)
```

NVIDIA GPU için `requirements.txt` içindeki `--extra-index-url` satırının yorumunu kaldırın.

`.env` değişkenleri:

| Değişken | Açıklama |
|---|---|
| `DATABASE_URL` | Neon doğrudan bağlantı (tablo oluşturma, toplu yükleme) |
| `DATABASE_URL_POOLED` | Neon pooler bağlantısı (Flask API) |
| `OPENSKY_CREDENTIALS_PATH` | OpenSky OAuth2 dosyası; boşsa OpenSky kapanır, harita adsb.lol ile çalışır |
| `STADIA_API_KEY` | Harita karoları; localhost'ta boş bırakılabilir, gerçek alan adında gerekir |
| `TRUSTED_PROXY_HOPS` | Ters vekil arkasındaysa vekil sayısı (doğrudan erişimde `0`) |

> **Bu depoda olmayanlar:** `.env`, `credentials.json` (gizli bilgiler), `data/` (BTS/FGVC verileri) ve
> `models/` (eğitilmiş modeller). Uygulamanın açılması için modellerin aşağıdaki adımlarla eğitilmiş olması
> ve `.env`'de bir Neon adresi bulunması gerekir. Model dosyalarını (`.joblib` pickle biçimindedir) yalnızca
> kendi eğittiğinizden ya da güvendiğiniz kaynaktan yükleyin.

## Çalıştırma

```powershell
python -m src.api.app --port 5000      # waitress; modeller açılışta bir kez yüklenir
python -m src.api.app --dev            # Flask geliştirme sunucusu
```

Tarayıcıda <http://127.0.0.1:5000/> — `/` ana sayfa · `/harita` canlı harita · `/gecikme` gecikme tahmini ·
`/tanima` uçak tanıma.

## Verileri hazırlama ve model eğitimi

<details>
<summary><b>Adım adım komutlar</b> (veri → veritabanı → modeller)</summary>

**1. Veri toplama**
```powershell
python -m src.ingest.load_bts          # data/raw/bts/*.zip → data/processed/bts_flights.parquet
python -m src.ingest.load_weather      # IEM METAR → data/processed/weather_hourly.parquet (load_bts'den sonra)
python -m src.ingest.load_aircraft_db  # OpenSky aircraft DB → data/processed/aircraft.parquet
python -m src.ingest.load_fgvc         # FGVC-Aircraft → data/raw/fgvc/ + sınıf dağılımı raporu
```
BTS "On-Time Performance" ZIP dosyaları `data/raw/bts/` klasörüne elle konur; birden çok ay desteklenir.

**2. Veritabanı**
```powershell
python -m src.db.init_db               # tabloları ve indeksleri oluştur (varsa dokunmaz)
python -m src.ingest.load_to_db        # Parquet → Neon (COPY); tablolar boşaltılıp yeniden doldurulur
python -m src.ingest.load_to_db flights  # yalnızca seçilen tablo(lar): flights, weather, aircraft
```

**3. Gecikme modeli**
```powershell
python -m src.ml.delay.export_data     # Neon → data/processed/delay_train_*.parquet (bir kez; --refresh ile yenile)
python -m src.ml.delay.train           # LogReg + LightGBM; çıktılar models/delay/ altında
python -m src.ml.delay.calibration     # kalibrasyon grafiği (yeniden eğitmeden)
```

**4. Fotoğraf sınıflandırma**
```powershell
python -m src.ml.vision.train select --arch efficientnet_b0  # train'de eğit, val'de epoch seç
python -m src.ml.vision.train select --arch resnet50
python -m src.ml.vision.train final    # en iyi mimari + epoch ile train+val'de yeniden eğit
python -m src.ml.vision.evaluate       # test seti — yalnızca en sonda, bir kez
```
Eğitim her epoch sonunda `models/vision/checkpoints/` altına checkpoint yazar; yarıda kalırsa aynı komuta
`--resume` eklenerek sürdürülür.

</details>

## REST API

| Yöntem | Endpoint | Açıklama |
|---|---|---|
| GET | `/api/health` | Veritabanı durumu, model sürümleri, günlük OpenSky kredi sayacı |
| GET | `/api/airports` | Desteklenen kalkış havalimanları |
| GET | `/api/flights/world` | Dünya genelindeki tüm uçaklar (sıkıştırılmış, `ETag` destekli) |
| GET | `/api/flights/live?lamin=&lomin=&lamax=&lomax=` | Bölgedeki canlı uçaklar + tip/tescil (OpenSky, önbellekli) |
| GET | `/api/flights/<icao24>/detail` | Seçili uçağın ayrıntısı |
| GET | `/api/flights/route/<çağrı kodu>` | Kalkış/varış rotası ve ilerleme |
| GET | `/api/aircraft/<icao24>` | Uçak veritabanı kaydı |
| POST | `/api/predict-delay` | JSON: `origin, dest, airline, date (YYYY-MM-DD), dep_time (HH:MM, yerel)`; isteğe bağlı `compare_times` |
| POST | `/api/classify-aircraft` | `multipart/form-data`, alan `image` (JPG/PNG, ≤ 10 MB) → en olası 3 aile |

Örnek:

```powershell
curl -X POST http://127.0.0.1:5000/api/predict-delay -H "Content-Type: application/json" `
     -d '{"origin":"ATL","dest":"BOS","airline":"DL","date":"2026-10-02","dep_time":"19:00"}'
```

Hatalar her zaman `{"error": {"code": "...", "message": "..."}}` biçimindedir.

## Güvenlik ve performans

- **Güvenlik başlıkları:** her HTML yanıtında nonce'lı `Content-Security-Policy`, `X-Frame-Options`,
  `nosniff`, `Referrer-Policy`, `Permissions-Policy`; `/api/` yanıtları `no-store`.
- **Hız sınırı:** veritabanına/diske yazan ve dış servislere giden endpoint'ler IP başına sınırlıdır
  (`config.RATE_LIMITS_PER_MIN`; aşılınca `429` + `Retry-After`).
- **Girdi doğrulama:** havalimanı/havayolu/tarih/saat/koordinat doğrulanır; yüklenen dosyanın uzantısı ve gerçek
  içeriği (JPG/PNG), boyutu ve piksel sayısı kontrol edilir (sıkıştırma bombası koruması).
- **Güvenli model yükleme:** görüntü modeli `torch.load(weights_only=True)` ile açılır.
- **Gizli bilgiler** yalnızca `.env` ve `credentials.json` içindedir ve git'e girmez.
- **Önbellek ve sıkıştırma:** gzip/brotli; CSS/JS adresleri `?v=` ile sürümlenip bir yıl, büyük model ve
  görseller bir gün önbelleğe alınır.
- **Dış servisler:** Google Fonts, jsDelivr/unpkg (Leaflet ve Lenis SRI ile), Stadia Maps ve Planespotters.net
  ziyaretçinin IP adresini ilgili servislerle paylaşır.
- Yüklenen fotoğraflar `data/uploads/` altında saklanır; kişisel veri içerebileceğinden düzenli temizleyin.

### Siteyi yayına alırken

İnternete açacaksanız önüne HTTPS sonlandıran bir ters vekil (nginx, Caddy, Cloudflare…) koyun, uygulamayı
yalnızca yerel porta bağlı bırakın ve `.env` içinde `TRUSTED_PROXY_HOPS=1` yapın. Harita karo anahtarını
(`STADIA_API_KEY`) Stadia panelinden alan adına kısıtlayın.

## Proje yapısı

```
config.py                 # tüm ayarlar (havalimanları, eşikler, önbellek süreleri, hız sınırları)
src/
├── api/                  # Flask uygulaması: app, routes, services, validation, ratelimit
├── web/                  # sayfalar, şablonlar, CSS/JS, 3B model ve görseller
├── services/             # adsb_feed, opensky_client/cache, metar_client, route_lookup
├── ml/
│   ├── delay/            # gecikme modeli: export_data, train, evaluate, calibration, predict
│   └── vision/           # uçak sınıflandırıcı: train, evaluate, predict
├── features/             # gecikme özellikleri ve hava durumu eşleme
├── ingest/               # BTS, METAR, uçak DB, FGVC, Neon'a toplu yükleme
└── db/                   # bağlantı, tablolar (SQLAlchemy), depo sınıfı
tools/blender/scene.py    # ana sayfa sahnesi (Blender Cycles): 787, ışık, kamera yolu
tools/encode_frames.py    # render karelerini masaüstü/mobil WebP dizisine çevirir
```

## Veri kaynakları, atıflar ve lisans

- Uçuş gecikmeleri: ABD BTS On-Time Performance; hava durumu: Iowa Environmental Mesonet ve aviationweather.gov (METAR).
- Canlı konumlar: [adsb.lol](https://adsb.lol) (ODbL), adsb.fi ve OpenSky Network; rotalar: adsbdb.com; fotoğraflar:
  Planespotters.net (kullanım koşulları gereği fotoğrafçıya ve sayfaya bağlanır).
- Fotoğraf modeli FGVC-Aircraft verisiyle eğitilir (yalnızca araştırma/ticari olmayan kullanım; veri bu depoda
  dağıtılmaz).
- Ana sayfadaki 3B model: “Boeing 787-8”, rocket0314 (Sketchfab), CC BY 4.0.
- Harita karoları: Stadia Maps, OpenMapTiles, OpenStreetMap katkıcıları (Stadia'nın ücretsiz planı ticari olmayan
  kullanım içindir).

**Lisans:** özel lisans, tüm hakları saklıdır — bkz. [`LICENSE`](LICENSE). Üçüncü taraf bileşenler kendi
lisanslarına tabidir.
