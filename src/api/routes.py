"""REST API endpoint'leri."""
import gzip
import logging
import re
import uuid
from datetime import date

from flask import Blueprint, current_app, jsonify, request
from PIL import Image, UnidentifiedImageError
from werkzeug.utils import secure_filename

import config
from src.api.ratelimit import rate_limited
from src.api.validation import (ApiError, parse_airline, parse_airport, parse_date, parse_dep_time,
                                parse_float_args, parse_icao24, require_fields)
from src.services.adsb_feed import AdsbFeedError, clean_callsign
from src.services.metar_client import to_icao
from src.services.opensky_cache import BBox, OpenSkyBudgetError
from src.services.opensky_client import OpenSkyError, OpenSkyRateLimitError

log = logging.getLogger(__name__)
bp = Blueprint("api", __name__, url_prefix="/api")

MAX_IMAGE_PIXELS = 40_000_000  # sıkıştırma bombası koruması
MAX_COMPARE_TIMES = 8
STATE_FIELDS = ["icao24", "callsign", "origin_country", "time_position", "latitude", "longitude",
                "baro_altitude",
                "geo_altitude", "velocity", "true_track", "vertical_rate", "on_ground", "squawk",
                "last_contact", "category"]
AIRCRAFT_FIELDS = ["registration", "typecode", "manufacturer", "model", "operator", "operator_icao"]


def services():
    return current_app.extensions["flight_delay"]


# --- genel ---

@bp.get("/health")
def health():
    svc = services()
    try:
        db_ok = svc.repo.ping()
    except Exception as exc:  # noqa: BLE001
        log.warning("DB sağlık kontrolü başarısız: %s", exc)
        db_ok = False
    return jsonify({
        "status": "ok" if db_ok else "degraded",
        "database": "ok" if db_ok else "error",
        "models": {"delay": svc.delay_predictor.version, "vision": svc.classifier.version},
        "opensky": svc.opensky.counter.snapshot() if svc.opensky else None,
    })


@bp.get("/airports")
def airports():
    items = [{"iata": a, "icao": to_icao(a), "name": config.AIRPORT_NAMES.get(a),
              "timezone": config.AIRPORT_TZ.get(a)}
             for a in services().delay_predictor.airports]
    return jsonify({"count": len(items), "airports": items})


# --- canlı uçuşlar ---

@bp.get("/flights/live")
def flights_live():
    svc = services()
    if svc.opensky is None:
        raise ApiError(503, "OpenSky yapılandırılmamış (OPENSKY_CREDENTIALS_PATH).", "opensky_unavailable")
    lamin, lomin, lamax, lomax = parse_float_args(request.args, ["lamin", "lomin", "lamax", "lomax"])
    try:
        bbox = BBox(lamin, lomin, lamax, lomax)
        states, meta = svc.opensky.get_states(bbox)
    except ValueError as exc:
        raise ApiError(400, str(exc), "invalid_bbox") from None
    except OpenSkyRateLimitError as exc:
        err = ApiError(429, str(exc), "opensky_rate_limited")
        err.retry_after = exc.retry_after_s
        raise err from None
    except OpenSkyBudgetError as exc:
        raise ApiError(503, str(exc), "opensky_budget_exhausted") from None
    except OpenSkyError as exc:
        log.error("OpenSky hatası: %s", exc)
        raise ApiError(502, "OpenSky verisi alınamadı.", "opensky_error") from None

    try:
        info = svc.aircraft_lookup.get_many([s["icao24"] for s in states])
    except Exception as exc:  # noqa: BLE001 — uçak bilgisi olmadan da konum verisi döner
        log.warning("Uçak bilgisi alınamadı: %s", exc)
        info, meta = {}, {**meta, "aircraft_info": "unavailable"}
    aircraft = []
    for s in states:
        row = {k: s.get(k) for k in STATE_FIELDS}
        a = info.get(s["icao24"])
        row.update({k: (a or {}).get(k) for k in AIRCRAFT_FIELDS})
        aircraft.append(row)
    return jsonify({"time": meta["opensky_time"], "count": len(aircraft), "aircraft": aircraft,
                    "cache": {k: v for k, v in meta.items() if k != "opensky_time"}})


@bp.get("/flights/world")
def flights_world():
    """Dünya genelindeki tüm uçaklar (adsb.lol), sıkıştırılmış satır biçiminde.
    Aynı görüntü için If-None-Match ile 304 döner; gövde gzip olarak önbellekten gönderilir.
    ?fresh=1 (yenile düğmesi) eski görüntü yerine yenisi alınana kadar bekler."""
    feed = services().adsb
    if feed is None:
        raise ApiError(503, "Canlı veri kaynağı yapılandırılmamış.", "feed_unavailable")
    try:
        snap = feed.snapshot(fresh=request.args.get("fresh") == "1")
    except AdsbFeedError as exc:
        log.error("adsb.lol verisi alınamadı: %s", exc)
        raise ApiError(502, "Canlı uçak verisi alınamadı.", "feed_error") from None
    if request.if_none_match.contains_weak(snap.etag.strip('"')):
        resp = current_app.response_class(status=304)
    elif "gzip" in request.accept_encodings:
        resp = current_app.response_class(snap.body_gzip, mimetype="application/json")
        resp.headers["Content-Encoding"] = "gzip"
    else:
        resp = current_app.response_class(gzip.decompress(snap.body_gzip), mimetype="application/json")
    resp.headers["ETag"] = snap.etag
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Vary"] = "Accept-Encoding"
    return resp


@bp.get("/flights/<icao24>/detail")
def flight_detail(icao24: str):
    """Seçili uçağın ayrıntısı: canlı kayıt (adsb.lol) + uçak veritabanı (operatör, model)."""
    code = parse_icao24(icao24)
    feed = services().adsb
    raw = None
    if feed is not None:
        try:
            raw = feed.snapshot().raw.get(code)
        except Exception:  # noqa: BLE001 — canlı veri yoksa yalnızca veritabanı bilgisi döner
            raw = None
    try:
        db = services().aircraft_lookup.get_many([code]).get(code) or {}
    except Exception as exc:  # noqa: BLE001
        log.warning("Uçak bilgisi alınamadı: %s", exc)
        db = {}
    if raw is None and not db:
        raise ApiError(404, f"{code} için bilgi bulunamadı.", "not_found")
    raw = raw or {}
    callsign = clean_callsign(raw.get("flight"))
    operator = raw.get("ownOp") or db.get("operator")
    if not operator and not (raw.get("dbFlags") or 0) & 1:
        operator = services().aircraft_lookup.airline_name(callsign)
    return jsonify({
        "icao24": code,
        "callsign": callsign,
        "registration": raw.get("r") or db.get("registration"),
        "typecode": raw.get("t") or db.get("typecode"),
        "description": raw.get("desc") or db.get("model"),
        "manufacturer": db.get("manufacturer"),
        "operator": operator,
        "operator_icao": db.get("operator_icao"),
        "year": raw.get("year"),
        "military": bool((raw.get("dbFlags") or 0) & 1),
        "squawk": raw.get("squawk"),
        "emergency": raw.get("emergency") if raw.get("emergency") not in (None, "none") else None,
        "ias_kt": raw.get("ias"), "mach": raw.get("mach"), "alt_geom_ft": raw.get("alt_geom"),
        "source": raw.get("type"),
    })


CALLSIGN_RE = re.compile(r"^[A-Z0-9]{2,8}$")


@bp.get("/flights/route/<callsign>")
@rate_limited("route", config.RATE_LIMITS_PER_MIN["route"])
def flight_route(callsign: str):
    """Çağrı kodunun tarife rotası; ?lat=&lon= verilirse ilerleme ve makullük de hesaplanır."""
    from src.services.route_lookup import RouteLookupError, progress
    code = callsign.strip().upper()
    if not CALLSIGN_RE.match(code):
        raise ApiError(400, "Geçersiz çağrı kodu.", "invalid_callsign")
    lookup = services().routes
    if lookup is None:
        raise ApiError(503, "Rota servisi yapılandırılmamış.", "route_unavailable")
    try:
        route = lookup.get(code)
    except RouteLookupError:
        raise ApiError(502, "Rota bilgisi şu an alınamıyor.", "route_error") from None
    if route is None:
        raise ApiError(404, f"{code} için rota bilgisi yok.", "not_found")
    body = dict(route)
    lat, lon = request.args.get("lat", type=float), request.args.get("lon", type=float)
    if lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180 and all(
            v is not None for a in (route["origin"], route["destination"]) for v in (a["lat"], a["lon"])):
        body["progress"] = progress(route, lat, lon)
    return jsonify(body)


@bp.get("/aircraft/<icao24>")
@rate_limited("aircraft", config.RATE_LIMITS_PER_MIN["aircraft"])
def aircraft(icao24: str):
    code = parse_icao24(icao24)
    row = services().repo.get_aircraft(code)
    if row is None:
        raise ApiError(404, f"{code} uçak veritabanında yok.", "not_found")
    return jsonify(row)


# --- gecikme tahmini ---

@bp.post("/predict-delay")
@rate_limited("predict", config.RATE_LIMITS_PER_MIN["predict"])
def predict_delay():
    svc = services()
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError(400, "JSON gövde bekleniyor.", "invalid_json")
    require_fields(data, ["origin", "dest", "airline", "date", "dep_time"])
    origin = parse_airport(data["origin"], "origin")
    dest = parse_airport(data["dest"], "dest")
    airline = parse_airline(data["airline"])
    flight_date = parse_date(data["date"])
    dep_time = parse_dep_time(data["dep_time"])
    compare = data.get("compare_times") or []
    if not isinstance(compare, list) or len(compare) > MAX_COMPARE_TIMES:
        raise ApiError(400, f"compare_times en fazla {MAX_COMPARE_TIMES} saatlik bir liste olmalı.",
                       "invalid_compare_times")
    compare_times = sorted({parse_dep_time(t) for t in compare} | {dep_time}) if compare else []

    predictor = svc.delay_predictor
    if origin not in predictor.airports:
        raise ApiError(400, f"Kalkış havalimanı desteklenmiyor: {origin}.", "unsupported_origin",
                       {"supported": predictor.airports})

    notes = []
    if dest not in predictor.builder.dests_:
        notes.append(f"Varış {dest} eğitim verisinde yok veya seyrek; 'OTHER' grubu kullanıldı.")
    if airline not in predictor.builder.airlines_:
        notes.append(f"Havayolu {airline} eğitim verisinde yok; 'OTHER' grubu kullanıldı.")
    days_ahead = (flight_date - date.today()).days
    if abs(days_ahead) > 1:
        notes.append("Hava durumu kalkış ve varış havalimanlarının güncel METAR'ından alınır; "
                     f"uçuş {abs(days_ahead)} gün {'sonra' if days_ahead > 0 else 'önce'} olduğu için "
                     "tahmin daha az güvenilirdir.")
    weather = svc.metar.get(origin)
    if weather is None:
        notes.append("Güncel METAR alınamadı; tahmin hava durumu olmadan yapıldı.")
    dest_weather = svc.metar.get(dest)
    if dest_weather is None:
        notes.append(f"{dest} için güncel METAR alınamadı; varış hava durumu kullanılmadı.")
    # FAA'nın anlık durumu yalnızca bugünkü uçuşlar için anlamlı; olasılığa katılmaz, uyarı olarak döner
    nas_alerts = None
    if svc.nas is not None and days_ahead == 0:
        nas_alerts = {"origin": svc.nas.get(origin), "dest": svc.nas.get(dest)}

    result = predictor.predict(origin, dest, airline, flight_date, dep_time, weather=weather or {},
                               dest_weather=dest_weather or {})
    weather_out = result.weather or None
    # Aynı rota ve hava durumuyla başka saatler (yalnızca yanıtta; veritabanına kaydedilmez)
    others = [t for t in compare_times if t != dep_time]
    probs = dict(zip(others, predictor.predict_times(origin, dest, airline, flight_date, others,
                                                     weather=weather or {},
                                                     dest_weather=dest_weather or {})))
    probs[dep_time] = result.probability
    time_profile = [{
        "dep_time": f"{t // 100:02d}:{t % 100:02d}", "selected": t == dep_time,
        "probability": round(probs[t], 4),
    } for t in compare_times]

    prediction_id = None
    try:
        prediction_id = svc.repo.save_delay_prediction(
            origin=origin, dest=dest, airline=airline, flight_date=flight_date, crs_dep_time=dep_time,
            probability=result.probability, weather=weather_out, model_version=result.model_version)
    except Exception as exc:  # noqa: BLE001 — kayıt başarısız olsa da tahmin döner
        log.error("Tahmin kaydedilemedi: %s", exc)
        notes.append("Tahmin veritabanına kaydedilemedi.")

    return jsonify({
        "origin": origin, "dest": dest, "airline": airline,
        "date": flight_date.isoformat(), "dep_time": f"{dep_time // 100:02d}:{dep_time % 100:02d}",
        "delay_probability": round(result.probability, 4),
        "is_delayed": bool(result.is_delayed), "threshold": round(result.threshold, 4),
        "weather": weather_out, "notes": notes, "time_profile": time_profile,
        "nas_alerts": nas_alerts,
        "model_version": result.model_version, "prediction_id": prediction_id,
    })


# --- fotoğraf sınıflandırma ---

def group_confusable(probs: dict[str, float], k: int = 3) -> list[dict]:
    """Birbirine çok benzeyen aileleri (config.CONFUSABLE_FAMILIES) tek satırda toplar;
    olasılıkları toplanır. En olası k satırı döner."""
    rows, used = [], set()
    for group in config.CONFUSABLE_FAMILIES:
        members = sorted((m for m in group if m in probs), key=lambda m: -probs[m])
        if len(members) > 1:
            used.update(members)
            rows.append({"label": " / ".join(members), "members": members,
                         "score": sum(probs[m] for m in members),
                         "breakdown": [{"label": m, "score": round(probs[m], 4)} for m in members]})
    rows += [{"label": c, "members": [c], "score": p, "breakdown": []}
             for c, p in probs.items() if c not in used]
    rows.sort(key=lambda r: -r["score"])
    top = rows[:k]
    for r in top:
        r["score"] = round(r["score"], 4)
        # Grup içinde olasılığı çok düşük üyeler ayrıntıda gösterilmez
        r["breakdown"] = [b for b in r["breakdown"] if b["score"] >= 0.01] if len(r["members"]) > 1 else []
        r["grouped"] = len(r["breakdown"]) > 1
        if r["grouped"]:
            r["label"] = " / ".join(b["label"] for b in r["breakdown"])
        else:
            r["label"] = r["breakdown"][0]["label"] if r["breakdown"] else r["members"][0]
    return top


def _read_image(file) -> tuple[Image.Image, str]:
    """Dosyayı doğrular: uzantı JPG/PNG, içerik de gerçekten JPEG/PNG ve uzantıyla uyumlu."""
    name = secure_filename(file.filename or "")
    ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
    allowed_exts = {e for exts in config.ALLOWED_IMAGE_FORMATS.values() for e in exts}
    if ext not in allowed_exts:
        raise ApiError(400, "Yalnızca JPG veya PNG dosyası yüklenebilir.", "unsupported_file_type")
    file.stream.seek(0, 2)
    size = file.stream.tell()
    file.stream.seek(0)
    if size == 0:
        raise ApiError(400, "Dosya boş.", "empty_file")
    if size > config.MAX_UPLOAD_MB * 1024 * 1024:
        raise ApiError(413, f"Dosya en fazla {config.MAX_UPLOAD_MB} MB olabilir.", "file_too_large")
    try:
        img = Image.open(file.stream)
        img.verify()
        file.stream.seek(0)
        img = Image.open(file.stream)
        fmt = img.format
        if img.width * img.height > MAX_IMAGE_PIXELS:
            raise ApiError(400, "Görüntü çözünürlüğü çok yüksek.", "image_too_large")
        img.load()
    except ApiError:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError):
        raise ApiError(400, "Dosya geçerli bir görüntü değil veya bozuk.", "invalid_image") from None
    if ext not in config.ALLOWED_IMAGE_FORMATS.get(fmt, set()):
        raise ApiError(400, f"Dosya içeriği ({fmt}) uzantıyla ({ext}) uyuşmuyor; yalnızca JPG/PNG.",
                       "unsupported_file_type")
    return img, name


@bp.post("/classify-aircraft")
@rate_limited("classify", config.RATE_LIMITS_PER_MIN["classify"])
def classify_aircraft():
    svc = services()
    file = request.files.get("image")
    if file is None or not file.filename:
        raise ApiError(400, "'image' alanında bir dosya bekleniyor (multipart/form-data).", "missing_file")
    img, name = _read_image(file)

    svc.upload_dir.mkdir(parents=True, exist_ok=True)
    stored = f"{uuid.uuid4().hex}_{name}"
    file.stream.seek(0)
    file.save(svc.upload_dir / stored)

    with svc.classifier_lock:  # GPU modeline eşzamanlı erişimi sırala
        probs = svc.classifier.probabilities(img)
    predictions = [{"label": label, "score": round(p, 4)}
                   for label, p in sorted(probs.items(), key=lambda kv: -kv[1])[:3]]

    classification_id, notes = None, []
    try:
        rel_path = (svc.upload_dir / stored).relative_to(config.BASE_DIR).as_posix() \
            if (svc.upload_dir / stored).is_relative_to(config.BASE_DIR) else str(svc.upload_dir / stored)
        classification_id = svc.repo.save_image_classification(
            file_path=rel_path, predictions=predictions, model_version=svc.classifier.version)
    except Exception as exc:  # noqa: BLE001
        log.error("Sınıflandırma kaydedilemedi: %s", exc)
        notes.append("Sonuç veritabanına kaydedilemedi.")

    return jsonify({"predictions": predictions, "top1": predictions[0],
                    "top_groups": group_confusable(probs),
                    "model_version": svc.classifier.version, "classification_id": classification_id,
                    "file": stored, "notes": notes})
