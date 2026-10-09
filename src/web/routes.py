"""Web arayüzü sayfaları. Veriler tarayıcıda /api/... endpoint'lerinden çekilir;
model başarı metrikleri sunucuda models/ altındaki rapor dosyalarından okunur."""
import json
import logging
from pathlib import Path

from flask import Blueprint, current_app, render_template

import config

log = logging.getLogger(__name__)
web = Blueprint("web", __name__, template_folder="templates", static_folder="static",
                static_url_path="/static")

def _read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("%s okunamadı: %s", path, exc)
        return None


def model_summary() -> dict:
    """Kaydedilmiş modellerin test metrikleri (Faz 3 ve 4 raporları). Dosya yoksa None."""
    delay = _read_json(config.DELAY_MODEL_DIR / "metrics.json") or {}
    chosen = next((m for name, m in delay.get("results", {}).items() if "★" in name), None)
    vision = _read_json(config.MODELS_DIR / "vision" / "test_metrics.json") or {}
    return {
        "delay_roc_auc": chosen["roc_auc"] if chosen else None,
        "vision_top1": vision.get("top1"),
        "vision_top3": vision.get("top3"),
    }


@web.app_template_filter("tr_num")
def tr_num(value, digits: int = 1) -> str:
    """0.712 → '0,712' (Türkçe ondalık virgül)."""
    return f"{value:.{digits}f}".replace(".", ",")


@web.app_template_filter("tr_pct")
def tr_pct(value, digits: int = 1) -> str:
    """0.9604 → '%96,0'."""
    return "%" + tr_num(value * 100, digits)


@web.app_context_processor
def _globals():
    return {"app_name": config.APP_NAME}


@web.get("/")
def index():
    svc = current_app.extensions["flight_delay"]
    classes = getattr(svc.classifier, "classes", None)
    airports = [{"iata": a, "name": config.AIRPORT_NAMES.get(a, a)} for a in svc.delay_predictor.airports]
    seq = list((Path(web.static_folder) / "seq" / "d").glob("*.webp"))
    n_frames = max((int(p.stem) for p in seq), default=0)
    # Kare adresleri sürümlü: dizi yeniden render edilince tarayıcı önbelleğindeki eski kareler kullanılmaz
    seq_v = max((int(p.stat().st_mtime) for p in seq), default=0)
    delay = _read_json(config.DELAY_MODEL_DIR / "metrics.json") or {}
    n_train = delay.get("n_train")
    return render_template("index.html", active="web.index", metrics=model_summary(),
                           n_airports=len(airports), airports=airports, n_frames=n_frames, seq_v=seq_v,
                           train_flights=(f"{n_train / 1e6:.2f}".replace(".", ",") + " milyon") if n_train else "geçmiş",
                           n_families=len(classes) if classes else None)


MONTHS_TR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos",
             "Eylül", "Ekim", "Kasım", "Aralık"]


def month_range_tr(period) -> str | None:
    """['2026-06-01', '2026-07-10'] → 'Haziran–Temmuz 2026'."""
    try:
        (y1, m1), (y2, m2) = [(int(d[:4]), int(d[5:7])) for d in period]
    except (TypeError, ValueError):
        return None
    if (y1, m1) == (y2, m2):
        return f"{MONTHS_TR[m1 - 1]} {y1}"
    first = MONTHS_TR[m1 - 1] + ("" if y1 == y2 else f" {y1}")
    return f"{first}–{MONTHS_TR[m2 - 1]} {y2}"


@web.get("/harita")
def harita():
    return render_template("harita.html", active="web.harita", poll_seconds=config.MAP_POLL_S,
                           tiles=config.MAP_TILES)


@web.get("/gecikme")
def gecikme():
    svc = current_app.extensions["flight_delay"]
    return render_template("gecikme.html", active="web.gecikme", metrics=model_summary(),
                           train_months=month_range_tr(getattr(svc.delay_predictor, "train_period", None)))


ARCH_NAMES = {"convnext_tiny": "ConvNeXt-Tiny", "efficientnet_b0": "EfficientNet-B0",
              "resnet50": "ResNet-50"}


@web.get("/tanima")
def tanima():
    clf = current_app.extensions["flight_delay"].classifier
    return render_template("tanima.html", active="web.tanima", metrics=model_summary(),
                           max_upload_mb=config.MAX_UPLOAD_MB,
                           n_classes=len(clf.classes),
                           arch_name=ARCH_NAMES.get(getattr(clf, "arch", ""), "CNN"),
                           confusable=[" / ".join(g) for g in config.CONFUSABLE_FAMILIES])
