"""METAR alanlarını ortak biçime getiren yardımcılar.

Hem geçmiş veri (IEM) hem canlı veri (aviationweather.gov) aynı fonksiyonlardan
geçer; böylece eğitim ve tahmin aynı özellik tanımını kullanır.

Ortak alanlar: temp_c, wind_kt, gust_kt, visibility_mi, ceiling_ft, precip
- ceiling_ft: BKN/OVC/VV olan en alçak katman (ft). Tavan yoksa None (sınırsız).
- precip: hava kodlarında yağış türü (DZ, RA, SN, ...) varsa True.
"""
from collections.abc import Iterable

CEILING_COVERS = {"BKN", "OVC", "VV", "OVX"}
PRECIP_CODES = {"DZ", "RA", "SN", "SG", "PL", "GR", "GS", "UP", "IC"}


def ceiling_ft(layers: Iterable[tuple[str | None, float | None]]) -> float | None:
    """(örtü, taban_ft) çiftlerinden bulut tavanını döner."""
    bases = [base for cover, base in layers
             if cover in CEILING_COVERS and base is not None and base == base]  # NaN değil
    return float(min(bases)) if bases else None


def has_precip(wxcodes: str | None) -> bool:
    """Hava kodları (ör. "-TSRA BR") istasyonda yağış içeriyor mu?

    Kodlar 2 harflik parçalardan oluşur (SH+RA, FZ+DZ). Yakın çevre (VC...)
    yağış sayılmaz.
    """
    if not isinstance(wxcodes, str) or not wxcodes:
        return False
    for token in wxcodes.split():
        token = token.lstrip("+-")
        if token.startswith("VC"):
            continue
        if any(token[i:i + 2] in PRECIP_CODES for i in range(0, len(token), 2)):
            return True
    return False


def f_to_c(temp_f: float | None) -> float | None:
    return None if temp_f is None else (temp_f - 32.0) * 5.0 / 9.0
