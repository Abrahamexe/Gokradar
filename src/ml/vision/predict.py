"""Uçak fotoğrafından aile tahmini: en olası 3 sınıf ve güven skorları."""
import io
from pathlib import Path

import torch
from PIL import Image

from src.ml.vision.common import MODEL_PATH, build_model, eval_transform


class AircraftClassifier:
    def __init__(self, path: Path = MODEL_PATH, device: str | None = None):
        if not path.exists():
            raise FileNotFoundError(f"{path} yok; önce `python -m src.ml.vision.train final` çalıştırın.")
        self.device = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))
        # weights_only=True: dosya yalnızca tensör ve sade tipler içerebilir (pickle ile kod çalıştırılamaz)
        ckpt = torch.load(path, map_location=self.device, weights_only=True)
        self.classes: list[str] = ckpt["classes"]
        self.version: str = ckpt["version"]
        self.arch: str = ckpt["arch"]
        self.model = build_model(ckpt["arch"], len(self.classes), pretrained=False)
        self.model.load_state_dict(ckpt["state_dict"])
        self.model.to(self.device).eval()
        self.transform = eval_transform(crop_banner=False)  # kullanıcı fotoğraflarında şerit yok

    @torch.no_grad()
    def _probs(self, image: Image.Image | str | Path | bytes) -> torch.Tensor:
        if isinstance(image, bytes):
            image = Image.open(io.BytesIO(image))
        elif isinstance(image, (str, Path)):
            image = Image.open(image)
        x = self.transform(image.convert("RGB")).unsqueeze(0).to(self.device)
        return torch.softmax(self.model(x).float(), dim=1)[0].cpu()

    def predict(self, image: Image.Image | str | Path | bytes, k: int = 3) -> list[dict]:
        scores, idx = self._probs(image).topk(k)
        return [{"label": self.classes[i], "score": round(float(s), 4)}
                for s, i in zip(scores.tolist(), idx.tolist())]

    def probabilities(self, image: Image.Image | str | Path | bytes) -> dict[str, float]:
        """Tüm aileler için olasılıklar (benzer aileleri birleştirmek için)."""
        return {c: float(p) for c, p in zip(self.classes, self._probs(image).tolist())}
