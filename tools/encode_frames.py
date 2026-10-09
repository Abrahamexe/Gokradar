"""Blender'dan çıkan PNG kareleri ana sayfanın kaydırma dizisine (WebP) çevirir.

Çıktı: src/web/static/seq/d/NNN.webp (masaüstü, 2560 px) ve seq/m/NNN.webp (mobil, 1280 px).
Var olan çıktılar atlanır; kaynak kare daha yeniyse yeniden üretilir.

Çalıştırma (proje kökünden):  python tools/encode_frames.py <png klasörü>
"""
import sys
from pathlib import Path

from PIL import Image

OUT = Path(__file__).resolve().parents[1] / "src" / "web" / "static" / "seq"
SIZES = {"d": (2560, 74), "m": (1280, 70)}   # klasör: (genişlik, WebP kalitesi)


def main(src: Path) -> None:
    frames = sorted(src.glob("f_*.png"))
    if not frames:
        sys.exit(f"{src} içinde f_*.png yok.")
    for key in SIZES:
        (OUT / key).mkdir(parents=True, exist_ok=True)
    done = 0
    for i, png in enumerate(frames, 1):
        n = int(png.stem.split("_")[1])
        img = None
        for key, (width, quality) in SIZES.items():
            out = OUT / key / f"{n:03d}.webp"
            if out.exists() and out.stat().st_mtime >= png.stat().st_mtime:
                continue
            img = img or Image.open(png).convert("RGB")
            frame = img if img.width == width else img.resize((width, round(img.height * width / img.width)), Image.LANCZOS)
            frame.save(out, "WEBP", quality=quality, method=6)
            done += 1
        if i % 30 == 0:
            print(f"  {i}/{len(frames)}", flush=True)
    for key in SIZES:
        size = sum(p.stat().st_size for p in (OUT / key).glob("*.webp"))
        print(f"{key}: {len(list((OUT / key).glob('*.webp')))} kare, {size / 1e6:.1f} MB")
    print(f"{done} dosya üretildi.")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
