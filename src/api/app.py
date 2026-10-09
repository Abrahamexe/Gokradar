"""Flask uygulaması: REST API (/api/...) + web arayüzü (/, /harita, /gecikme, /tanima).

Çalıştırma (proje kökünden):  python -m src.api.app [--port 5000]
Modeller ve bağlantılar açılışta bir kez yüklenir (her istekte değil).
"""
import argparse
import logging
import secrets
import sys
from functools import lru_cache
from logging.handlers import RotatingFileHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from flask import Flask, g, jsonify, render_template, request  # noqa: E402
from flask_compress import Compress  # noqa: E402
from werkzeug.exceptions import HTTPException  # noqa: E402
from werkzeug.middleware.proxy_fix import ProxyFix  # noqa: E402

import config  # noqa: E402
from src.api.ratelimit import RateLimiter  # noqa: E402
from src.api.routes import bp  # noqa: E402
from src.api.validation import ApiError  # noqa: E402
from src.web.routes import web  # noqa: E402

LOG_DIR = config.BASE_DIR / "logs"
STATIC_DIR = Path(web.static_folder)
LONG_CACHED_DIRS = ("/static/seq/", "/static/img/")   # büyük, nadiren değişen dosyalar


def build_csp(nonce: str) -> str:
    """Sayfa yalnızca bilinen kaynaklardan kod/görsel yükleyebilir; inline script'ler nonce ister."""
    return "; ".join([
        "default-src 'self'",
        f"script-src 'self' 'nonce-{nonce}' https://unpkg.com",
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://unpkg.com",
        "font-src 'self' https://fonts.gstatic.com",
        "img-src 'self' data: blob: https://tiles.stadiamaps.com https://*.plnspttrs.net",
        "connect-src 'self' https://api.planespotters.net",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ])


@lru_cache(maxsize=512)
def _static_version(filename: str) -> str | None:
    """Statik dosyanın değişim zamanı: URL'ye ?v= eklenir, böylece dosya değişince tarayıcı yenisini alır."""
    try:
        path = (STATIC_DIR / filename).resolve()
        if STATIC_DIR.resolve() not in path.parents:
            return None
        return str(int(path.stat().st_mtime))
    except OSError:
        return None


def create_app(services=None) -> Flask:
    """services verilmezse gerçek servisler (modeller, Neon pooler, OpenSky) oluşturulur."""
    if services is None:
        from src.api.services import build_services
        services = build_services()

    app = Flask(__name__, static_folder=None)  # statik dosyalar web blueprint'inde
    app.json.ensure_ascii = False
    app.json.sort_keys = False
    # Multipart ek yükü için küçük pay; dosya boyutu ayrıca tam olarak kontrol edilir.
    app.config["MAX_CONTENT_LENGTH"] = config.MAX_UPLOAD_MB * 1024 * 1024 + 64 * 1024
    # .glb/.dat zaten sıkıştırılmış (kazanç ihmal edilebilir); metin ve HDR haritası sıkıştırılır.
    app.config["COMPRESS_MIMETYPES"] = ["text/html", "text/css", "text/xml", "text/javascript",
                                        "application/javascript", "application/json", "image/svg+xml",
                                        "image/vnd.radiance"]
    # Statik dosyalar akış olarak döner: gzip akışta da açık olsun, endpoint adı blueprint önekli.
    app.config["COMPRESS_ALGORITHM_STREAMING"] = ["br", "zstd", "gzip", "deflate"]
    app.config["COMPRESS_STREAMING_ENDPOINT_CONDITIONAL"] = ["web.static"]
    if config.TRUSTED_PROXY_HOPS:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=config.TRUSTED_PROXY_HOPS,
                                x_proto=config.TRUSTED_PROXY_HOPS, x_host=config.TRUSTED_PROXY_HOPS)
    Compress(app)
    app.extensions["flight_delay"] = services
    app.extensions["rate_limiter"] = RateLimiter()
    app.register_blueprint(bp)
    app.register_blueprint(web)

    @app.before_request
    def _csp_nonce():
        g.csp_nonce = secrets.token_urlsafe(16)

    @app.context_processor
    def _csp_context():
        return {"csp_nonce": getattr(g, "csp_nonce", "")}

    @app.url_defaults
    def _version_static_urls(endpoint, values):
        # Yalnızca CSS/JS: görseller CSS içinden sürümsüz istenir, preload ile aynı adreste kalmalı.
        filename = values.get("filename", "")
        if endpoint == "web.static" and "v" not in values and filename.endswith((".css", ".js")):
            version = _static_version(filename)
            if version:
                values["v"] = version

    @app.after_request
    def _headers(resp):
        h = resp.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("X-Frame-Options", "DENY")
        # Planespotters API Origin/Referer bekler; bu yüzden tamamen kapatılmaz.
        h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        if request.is_secure:
            h.setdefault("Strict-Transport-Security", "max-age=31536000")
        path = request.path
        if path.startswith("/static/"):
            if "v" in request.args:   # sürümlü adres: içerik değişince adres de değişir
                h["Cache-Control"] = "public, max-age=31536000, immutable"
            elif path.startswith(LONG_CACHED_DIRS):
                h["Cache-Control"] = "public, max-age=86400"
        elif path.startswith("/api/"):
            h.setdefault("Cache-Control", "no-store")   # tahminler ve canlı veri aracılarda saklanmasın
        elif resp.mimetype == "text/html" and hasattr(g, "csp_nonce"):
            h["Content-Security-Policy"] = build_csp(g.csp_nonce)
        return resp

    @app.errorhandler(ApiError)
    def _api_error(err: ApiError):
        resp = jsonify(err.to_dict())
        resp.status_code = err.status
        if getattr(err, "retry_after", None):
            resp.headers["Retry-After"] = str(err.retry_after)
        return resp

    @app.errorhandler(HTTPException)
    def _http_error(err: HTTPException):
        if not request.path.startswith("/api/"):  # web sayfaları HTML hata sayfası alır
            return render_template("error.html", code=err.code,
                                   message="Aradığınız sayfa bulunamadı." if err.code == 404
                                   else err.description), err.code
        messages = {404: "Endpoint bulunamadı.", 405: "Bu endpoint için yöntem desteklenmiyor.",
                    413: f"İstek çok büyük; dosya en fazla {config.MAX_UPLOAD_MB} MB olabilir."}
        body = {"error": {"code": err.name.lower().replace(" ", "_"),
                          "message": messages.get(err.code, err.description)}}
        return jsonify(body), err.code

    @app.errorhandler(Exception)
    def _unexpected(err: Exception):
        app.logger.exception("Beklenmeyen hata")
        return jsonify({"error": {"code": "internal_error", "message": "Sunucu hatası."}}), 500

    return app


def setup_logging() -> None:
    """Konsol + logs/opensky.log (günlük kredi sayacı satırları)."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    LOG_DIR.mkdir(exist_ok=True)
    handler = RotatingFileHandler(LOG_DIR / "opensky.log", maxBytes=2_000_000, backupCount=5,
                                  encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.getLogger("opensky").addHandler(handler)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--dev", action="store_true", help="Flask geliştirme sunucusunu kullan")
    args = parser.parse_args()
    setup_logging()
    app = create_app()
    try:
        if args.dev:
            raise ImportError
        from waitress import serve
    except ImportError:
        # Yeniden yükleyici kapalı: modeller iki kez yüklenmesin. Geliştirme sunucusu herkese açılmamalı.
        app.run(host=args.host, port=args.port, threaded=True, use_reloader=False)
    else:
        logging.getLogger(__name__).info("waitress: http://%s:%d", args.host, args.port)
        # Tek süreç, çok iş parçacığı: modeller bir kez yüklenir; gövde sınırı Flask'ınkiyle aynı.
        serve(app, host=args.host, port=args.port, threads=8, ident=None,
              max_request_body_size=app.config["MAX_CONTENT_LENGTH"])


if __name__ == "__main__":
    main()
