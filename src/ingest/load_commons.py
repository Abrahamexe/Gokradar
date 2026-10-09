"""Wikimedia Commons'tan FGVC-Aircraft'ta (2013) bulunmayan güncel uçak tiplerinin
fotoğraflarını toplar: 787, A350, A220, 737 MAX, savaş uçakları vb. Ayrıca bazı mevcut
ailelere (A320, A330, 747, 777) yeni nesil varyantların fotoğraflarını ekler.

Adımlar (her biri kaldığı yerden devam eder; yarıda kesilirse aynı komut yeniden çalıştırılır):
  collect   kategori ağacı taranır (kabin, kokpit, maket, parça vb. alt kategoriler elenir),
            sınıf başına rastgele örnek seçilir, lisans/yazar bilgisi alınır → manifest.csv
  download  küçük boyutlu kopyalar (en uzun kenar 640 px) images/ altına indirilir
  filter    ImageNet ön eğitimli bir modelle "uçak dışı" görüntüler (kabin, motor yakını,
            belge vb.) elenir → manifest'e keep kolonu
  split     fotoğrafçıya (yükleyen kullanıcı) göre train/val/test bölünür (büyük arşivler
            ardışık parçalara ayrılır): aynı kişinin aynı
            gün çektiği neredeyse aynı kareler farklı bölümlere düşmez

Çalıştırma (proje kökünden):
  python -m src.ingest.load_commons            # tüm adımlar
  python -m src.ingest.load_commons download   # yalnızca bir adım
Lisans ve yazar bilgileri manifest.csv'de tutulur (CC BY / CC BY-SA atıf gereği).
"""
import argparse
import hashlib
import heapq
import io
import random
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import requests  # noqa: E402
from PIL import Image  # noqa: E402

import config  # noqa: E402

API_URL = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "GOKRADAR-dataset/1.0 (aircraft recognition training; https://github.com/Abrahamexe)"
IMAGES_DIR = config.COMMONS_DIR / "images"
MANIFEST_PATH = config.COMMONS_DIR / "manifest.csv"
CATEGORY_CACHE = config.COMMONS_DIR / "category_files.parquet"

# Etiket → kök kategoriler. Etiket FGVC'deki bir aileyle aynıysa o aileye örnek eklenir.
# (kök kategoriler, sınıf başına hedef görüntü sayısı)
NEW_FAMILIES = {
    "Boeing 787": ["Boeing 787"],
    "A350": ["Airbus A350"],
    "A220": ["Airbus A220"],
    "Boeing 737 MAX": ["Boeing 737 MAX"],
    "Embraer E-Jet E2": ["Embraer E-Jets E2"],
    "Superjet 100": ["Sukhoi Superjet 100"],
    "C919": ["Comac C919"],
    "F-35": ["Lockheed Martin F-35 Lightning II"],
    "F-22": ["Lockheed Martin F-22 Raptor"],
    "F-15": ["F-15 Eagle"],
    "Rafale": ["Dassault Rafale"],
    "Gripen": ["Saab 39 Gripen"],
    "Su-27/30/35": ["Sukhoi Su-27", "Sukhoi Su-30", "Sukhoi Su-35"],
    "MiG-29": ["Mikoyan MiG-29"],
    "C-17": ["Boeing C-17 Globemaster III"],
    "A400M": ["Airbus A400M Atlas"],
    "PC-12": ["Pilatus PC-12"],
    "Bayraktar TB2": ["Bayraktar TB2"],
}
EXTRA_FOR_EXISTING = {
    "A320": ["Airbus A320neo family"],
    "A330": ["Airbus A330neo"],
    "Boeing 747": ["Boeing 747-8"],
    "Boeing 777": ["Boeing 777X"],
}
PER_NEW_CLASS = 500
PER_EXISTING_CLASS = 200
MIN_CLASS_IMAGES = 80        # filtreden sonra bundan az kalan yeni sınıf eğitime alınmaz
OVERSAMPLE = 1.35            # filtrede elenecekler için fazladan indirilen pay
MAX_CATEGORIES_PER_CLASS = 700
CRAWL_PARALLEL = 2           # aynı anda sorgulanan kategori sayısı
THUMB_WIDTH = 960            # Commons'ın standart küçük resim genişliklerinden biri
SAVE_MAX_SIDE = 640
SPLIT_FRACTIONS = {"train": 0.70, "val": 0.15, "test": 0.15}
SEED = 42
SPLIT_CHUNK = 20            # büyük yükleyici gruplarının bölündüğü parça boyu

# Dış görünüş dışı içerik barındıran alt kategoriler (sözcük sınırıyla: "art" ≠ "Martin")
EXCLUDE_RE = re.compile(
    r"\b(cabins?|cockpits?|interiors?|seats?|models?|toys?|parts?|engines?|undercarriages?|"
    r"landing gear|wheels?|windows?|window views|videos?|production|assembly|factory|art|"
    r"stamps?|drawings?|diagrams?|accidents?|incidents?|crash(es)?|wrecks?|simulators?|"
    r"galleys?|crew|aircrew|people|pilots?|other subjects|logos?|insignia|patches|weapons?|"
    r"missiles?|ordnance|parachutes?|instruments?|radars?|N011|tail art|nose art)\b", re.I)
# " by ..." gruplamalarından bu ikisi dosya içermeyen tekrarlar ya da gereksiz dallanma
SKIP_BY_RE = re.compile(r" by (orientation|view|name|construction number|customer code|"
                        r"year of manufacture|type of operator|condition)$", re.I)
PRIORITY_BY_RE = re.compile(r" by (registration|serial number|variant)$|^\S+ \(aircraft\)$", re.I)

ALLOWED_MIME = {"image/jpeg", "image/png"}

session = requests.Session()
session.headers["User-Agent"] = USER_AGENT


def api(**params) -> dict:
    params = {"action": "query", "format": "json", "formatversion": 2, **params}
    for attempt in range(8):
        try:
            r = session.get(API_URL, params=params, timeout=30)
            if r.status_code == 429 or r.status_code >= 500:
                retry_after = r.headers.get("Retry-After", "")
                raise requests.HTTPError(f"HTTP {r.status_code}",
                                         response=r if retry_after.isdigit() else None)
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as exc:
            resp = getattr(exc, "response", None)
            wait = max(2 ** (attempt + 1), int(resp.headers["Retry-After"]) if resp is not None else 0)
            print(f"    API hatası ({exc}); {wait} sn sonra tekrar", flush=True)
            time.sleep(wait)
    raise RuntimeError("Commons API'ye ulaşılamadı.")


def members(category: str) -> tuple[list[str], list[str]]:
    """(alt kategoriler, dosyalar) — tek sorguda, tüm sayfalar dolaşılarak."""
    subcats, files, cont = [], [], {}
    while True:
        data = api(list="categorymembers", cmtitle=category, cmtype="subcat|file",
                   cmprop="title|type", cmlimit=500, **cont)
        for m in data["query"]["categorymembers"]:
            (subcats if m["type"] == "subcat" else files).append(m["title"])
        if "continue" not in data:
            return subcats, files
        cont = data["continue"]


def crawl(roots: list[str], wanted: int, pool: ThreadPoolExecutor) -> list[str]:
    """Kök kategorilerden başlayarak dosya başlıklarını toplar. Kategoriler paralel gruplar
    halinde sorgulanır; tescil/seri no kategorileri önce gezilir (uçak başına ayrı kategori
    → çeşitlilik)."""
    seen, files = set(), set()
    heap = [(0, 0, f"Category:{r}") for r in roots]
    while heap and len(seen) < MAX_CATEGORIES_PER_CLASS and len(files) < wanted * 3:
        batch = []
        while heap and len(batch) < CRAWL_PARALLEL:
            item = heapq.heappop(heap)
            if item[2] not in seen:
                seen.add(item[2])
                batch.append(item)
        for (_, depth, _), (subcats, found) in zip(batch, pool.map(lambda it: members(it[2]), batch)):
            files.update(t for t in found if t.lower().endswith((".jpg", ".jpeg", ".png")))
            if depth >= 5:
                continue
            for sub in subcats:
                name = sub.removeprefix("Category:")
                if sub in seen or EXCLUDE_RE.search(name) or SKIP_BY_RE.search(name):
                    continue
                heapq.heappush(heap, (0 if PRIORITY_BY_RE.search(name) else 1, depth + 1, sub))
    print(f"    {len(seen)} kategori, {len(files)} dosya", flush=True)
    return sorted(files)


def image_info(titles: list[str]) -> list[dict]:
    rows = []
    for i in range(0, len(titles), 50):
        data = api(titles="|".join(titles[i:i + 50]), prop="imageinfo",
                   iiprop="url|user|mime|size|extmetadata", iiurlwidth=THUMB_WIDTH,
                   iiextmetadatafilter="LicenseShortName|Artist|LicenseUrl")
        for page in data["query"]["pages"]:
            info = (page.get("imageinfo") or [None])[0]
            if not info or info.get("mime") not in ALLOWED_MIME or min(info["width"], info["height"]) < 300:
                continue
            meta = info.get("extmetadata", {})
            artist = re.sub(r"<[^>]+>", "", meta.get("Artist", {}).get("value", "")).strip()
            rows.append({"title": page["title"], "user": info.get("user", ""),
                         "thumb_url": info.get("thumburl") or info["url"],
                         "page_url": info.get("descriptionurl", ""),
                         "license": meta.get("LicenseShortName", {}).get("value", ""),
                         "license_url": meta.get("LicenseUrl", {}).get("value", ""),
                         "artist": artist[:200]})
    return rows


def file_id(title: str) -> str:
    return hashlib.sha1(title.encode("utf-8")).hexdigest()[:16]


def cmd_collect() -> None:
    config.COMMONS_DIR.mkdir(parents=True, exist_ok=True)
    done = pd.read_csv(MANIFEST_PATH) if MANIFEST_PATH.exists() else pd.DataFrame()
    done_labels = set(done["label"]) if len(done) else set()
    plan = [(lbl, roots, PER_NEW_CLASS, True) for lbl, roots in NEW_FAMILIES.items()] + \
           [(lbl, roots, PER_EXISTING_CLASS, False) for lbl, roots in EXTRA_FOR_EXISTING.items()]
    rng = random.Random(SEED)
    pool = ThreadPoolExecutor(max_workers=CRAWL_PARALLEL)
    parts = [done] if len(done) else []
    taken = set(done["title"]) if len(done) else set()
    for label, roots, target, is_new in plan:
        if label in done_labels:
            continue
        print(f"[collect] {label} ← {', '.join(roots)}", flush=True)
        titles = [t for t in crawl(roots, target, pool) if t not in taken]
        rng.shuffle(titles)
        titles = titles[:int(target * OVERSAMPLE)]
        rows = image_info(titles)
        df = pd.DataFrame(rows)
        if df.empty:
            print("    görüntü bulunamadı", flush=True)
            continue
        df["label"], df["new_family"] = label, is_new
        df["id"] = df["title"].map(file_id)
        taken.update(df["title"])
        parts.append(df)
        # Her sınıftan sonra kaydedilir: kesilirse tamamlanan sınıflar tekrar taranmaz
        pd.concat(parts, ignore_index=True).to_csv(MANIFEST_PATH, index=False)
        print(f"    {len(df)} görüntü manifest'e eklendi", flush=True)


def _download(row) -> bool:
    path = IMAGES_DIR / f"{row.id}.jpg"
    if path.exists():
        return True
    for attempt in range(5):
        try:
            r = session.get(row.thumb_url, timeout=60)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(2 ** attempt * 2)
                continue
            r.raise_for_status()
            img = Image.open(io.BytesIO(r.content)).convert("RGB")
            img.thumbnail((SAVE_MAX_SIDE, SAVE_MAX_SIDE), Image.LANCZOS)
            tmp = path.with_suffix(".part")
            img.save(tmp, "JPEG", quality=92)
            tmp.replace(path)
            return True
        except (requests.RequestException, OSError):
            time.sleep(2 ** attempt)
    return False


def cmd_download() -> None:
    df = pd.read_csv(MANIFEST_PATH)
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    todo = [r for r in df.itertuples() if not (IMAGES_DIR / f"{r.id}.jpg").exists()]
    print(f"[download] {len(df) - len(todo)}/{len(df)} hazır, {len(todo)} indirilecek", flush=True)
    failed = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        for i, ok in enumerate(pool.map(_download, todo), 1):
            failed += not ok
            if i % 200 == 0 or i == len(todo):
                print(f"    {i}/{len(todo)} (başarısız {failed})", flush=True)


# ImageNet'te uçak dış görünüşüne karşılık gelen sınıflar
AIRCRAFT_IMAGENET = {404: "airliner", 895: "warplane", 908: "wing", 405: "airship",
                     812: "space shuttle", 744: "projectile"}
KEEP_MIN_PROB = 0.3            # altı: uçak küçük kalmış ya da yakın çekim (elle örnekleyerek seçildi)


def cmd_filter() -> None:
    import torch
    from torchvision import models

    df = pd.read_csv(MANIFEST_PATH)
    df["downloaded"] = [(IMAGES_DIR / f"{i}.jpg").exists() for i in df["id"]]
    weights = models.ConvNeXt_Tiny_Weights.IMAGENET1K_V1
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = models.convnext_tiny(weights=weights).to(device).eval()
    tf = weights.transforms()
    idx = torch.tensor(list(AIRCRAFT_IMAGENET))
    scores = np.full(len(df), np.nan)
    ids = df["id"].tolist()
    print(f"[filter] {int(df['downloaded'].sum())} görüntü taranıyor ({device})", flush=True)
    with torch.no_grad():
        batch, pos = [], []
        for n, (i, ok) in enumerate(zip(ids, df["downloaded"])):
            if ok:
                batch.append(tf(Image.open(IMAGES_DIR / f"{i}.jpg").convert("RGB")))
                pos.append(n)
            if batch and (len(batch) == 64 or n == len(ids) - 1):
                p = torch.softmax(model(torch.stack(batch).to(device)), 1).cpu()
                scores[pos] = p[:, idx].sum(1).numpy()
                batch, pos = [], []
    df["aircraft_score"] = scores.round(4)
    df["keep"] = df["downloaded"] & (df["aircraft_score"] >= KEEP_MIN_PROB)
    df.to_csv(MANIFEST_PATH, index=False)
    summary = df.groupby("label").agg(indirilen=("downloaded", "sum"), kalan=("keep", "sum"))
    print(summary.to_string())


def cmd_split() -> None:
    """Sınıf içinde yükleyen kullanıcılar gruplanır; gruplar oranlara göre doldurulur."""
    df = pd.read_csv(MANIFEST_PATH)
    df["split"] = ""
    rng = random.Random(SEED)
    for label, part in df[df["keep"]].groupby("label"):
        target = PER_NEW_CLASS if part["new_family"].iloc[0] else PER_EXISTING_CLASS
        part = part.sample(frac=1, random_state=SEED).head(target)
        if part["new_family"].iloc[0] and len(part) < MIN_CLASS_IMAGES:
            print(f"  {label}: yalnızca {len(part)} görüntü, eğitime alınmadı")
            continue
        groups = []
        for _, g in part.groupby(part["user"].fillna("?")):
            # Tek yükleyicinin büyük arşivi bir bölümü tek başına doldurmasın: sıralı dosya
            # adlarına göre parçalanır (aynı çekimin ardışık kareleri aynı parçada kalır)
            idx = g.sort_values("title").index
            groups += [idx[i:i + SPLIT_CHUNK] for i in range(0, len(idx), SPLIT_CHUNK)] \
                if len(idx) > SPLIT_CHUNK else [idx]
        groups = list(enumerate(groups))
        rng.shuffle(groups)
        groups.sort(key=lambda g: len(g[1]))          # küçük gruplar önce: oranlar daha dengeli
        n, counts = len(part), {s: 0 for s in SPLIT_FRACTIONS}
        for _, idx in groups:
            # Hedefine oranla en çok eksiği olan bölüme
            split = max(SPLIT_FRACTIONS, key=lambda s: SPLIT_FRACTIONS[s] * n - counts[s])
            df.loc[idx, "split"] = split
            counts[split] += len(idx)
    df.to_csv(MANIFEST_PATH, index=False)
    table = df[df["split"] != ""].pivot_table(index="label", columns="split", values="id",
                                              aggfunc="count", fill_value=0)
    print(table[list(SPLIT_FRACTIONS)].to_string())
    print(f"Toplam: {(df['split'] != '').sum()} görüntü")


STEPS = {"collect": cmd_collect, "download": cmd_download, "filter": cmd_filter, "split": cmd_split}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("step", nargs="?", choices=list(STEPS), help="yalnızca bu adım (boş: hepsi)")
    args = parser.parse_args()
    for name in ([args.step] if args.step else STEPS):
        STEPS[name]()


if __name__ == "__main__":
    main()
