"""Fotoğraf sınıflandırma — eğitim, değerlendirme ve tahminin paylaştığı parçalar."""
import numpy as np
import pandas as pd
import torch
from PIL import Image
from torch import nn
from torch.utils.data import Dataset
from torchvision import models, transforms
from torchvision.datasets import FGVCAircraft

import config

IMG_SIZE = 320
IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)
FGVC_BANNER_PX = 20  # FGVC görüntülerinin altındaki telif şeridi (yalnızca veri setinde)

MODEL_DIR = config.MODELS_DIR / "vision"
MODEL_PATH = MODEL_DIR / "aircraft_classifier.pt"

ARCHS = ("convnext_tiny", "efficientnet_b0", "resnet50")
# Veri seti sürümü: seçim (select) sonuçları yalnızca aynı veriyle eğitilmiş final için geçerli
DATASET_VERSION = "fgvc+commons-v1"
COMMONS_MANIFEST = config.COMMONS_DIR / "manifest.csv"
COMMONS_IMAGES = config.COMMONS_DIR / "images"


class CropBanner:
    """Görüntünün altından sabit yükseklikte şerit keser."""
    def __init__(self, pixels: int = FGVC_BANNER_PX):
        self.pixels = pixels

    def __call__(self, img):
        w, h = img.size
        return img.crop((0, 0, w, h - self.pixels))


def train_transform(crop_banner: bool = True):
    steps = [CropBanner()] if crop_banner else []
    return transforms.Compose(steps + [
        transforms.RandomResizedCrop(IMG_SIZE, scale=(0.6, 1.0), ratio=(0.75, 1.6)),
        transforms.RandomHorizontalFlip(),
        transforms.RandomRotation(10),
        transforms.ColorJitter(brightness=0.2, contrast=0.2, saturation=0.2, hue=0.02),
        transforms.ToTensor(),
        transforms.Normalize(IMAGENET_MEAN, IMAGENET_STD),
    ])


def eval_transform(crop_banner: bool = True):
    """crop_banner=False: kullanıcı fotoğrafları (şerit yok)."""
    steps = [CropBanner()] if crop_banner else []
    return transforms.Compose(steps + [
        transforms.Resize(IMG_SIZE),
        transforms.CenterCrop(IMG_SIZE),
        transforms.ToTensor(),
        transforms.Normalize(IMAGENET_MEAN, IMAGENET_STD),
    ])


class AircraftDataset(Dataset):
    """FGVC-Aircraft (aile düzeyi, 70 sınıf) + Wikimedia Commons'tan güncel tipler.

    split: train, val, trainval, test. Commons manifest'i yoksa yalnızca FGVC kullanılır.
    FGVC görüntülerinin altındaki telif şeridi kesilir; Commons görüntülerinde şerit yoktur.
    """
    def __init__(self, split: str, train: bool):
        fgvc = FGVCAircraft(root=str(config.FGVC_DIR), split=split, annotation_level="family",
                            download=False)
        samples = [(path, fgvc.classes[y], True) for path, y in zip(fgvc._image_files, fgvc._labels)]
        if COMMONS_MANIFEST.exists():
            df = pd.read_csv(COMMONS_MANIFEST, keep_default_na=False)
            wanted = ("train", "val") if split == "trainval" else (split,)
            df = df[df["split"].isin(wanted)]
            samples += [(str(COMMONS_IMAGES / f"{i}.jpg"), label, False)
                        for i, label in zip(df["id"], df["label"])]
        # Sınıf listesi bölümden bağımsız olsun diye tüm manifest'ten kurulur
        self.classes: list[str] = all_classes()
        index = {c: i for i, c in enumerate(self.classes)}
        self.paths = [p for p, _, _ in samples]
        self.labels = [index[c] for _, c, _ in samples]
        self.from_fgvc = [f for _, _, f in samples]
        make = train_transform if train else eval_transform
        self.tf_fgvc, self.tf_plain = make(crop_banner=True), make(crop_banner=False)

    def __len__(self) -> int:
        return len(self.paths)

    def __getitem__(self, i):
        img = Image.open(self.paths[i]).convert("RGB")
        tf = self.tf_fgvc if self.from_fgvc[i] else self.tf_plain
        return tf(img), self.labels[i]


def all_classes() -> list[str]:
    """FGVC aileleri + Commons'ta en az bir bölüme atanmış yeni aileler (alfabetik)."""
    fgvc = FGVCAircraft(root=str(config.FGVC_DIR), split="test", annotation_level="family",
                        download=False).classes
    extra = set()
    if COMMONS_MANIFEST.exists():
        df = pd.read_csv(COMMONS_MANIFEST, keep_default_na=False)
        extra = set(df.loc[df["split"] != "", "label"])
    return sorted(set(fgvc) | extra)


def aircraft_dataset(split: str, train: bool) -> AircraftDataset:
    return AircraftDataset(split, train)


def class_weights(labels, num_classes: int) -> torch.Tensor:
    """Ters frekans ağırlıkları, ortalaması 1: w_c = N / (K * n_c)."""
    counts = np.bincount(np.asarray(labels), minlength=num_classes).astype(np.float64)
    weights = len(labels) / (num_classes * np.maximum(counts, 1))
    return torch.tensor(weights, dtype=torch.float32)


def build_model(arch: str, num_classes: int, pretrained: bool = True) -> nn.Module:
    if arch == "convnext_tiny":
        weights = models.ConvNeXt_Tiny_Weights.IMAGENET1K_V1 if pretrained else None
        model = models.convnext_tiny(weights=weights)
        model.classifier[2] = nn.Linear(model.classifier[2].in_features, num_classes)
    elif arch == "efficientnet_b0":
        weights = models.EfficientNet_B0_Weights.IMAGENET1K_V1 if pretrained else None
        model = models.efficientnet_b0(weights=weights)
        model.classifier[1] = nn.Linear(model.classifier[1].in_features, num_classes)
    elif arch == "resnet50":
        weights = models.ResNet50_Weights.IMAGENET1K_V2 if pretrained else None
        model = models.resnet50(weights=weights)
        model.fc = nn.Linear(model.fc.in_features, num_classes)
    else:
        raise ValueError(f"Bilinmeyen mimari: {arch}")
    return model


def head_and_last_blocks(model: nn.Module, arch: str) -> tuple[list[nn.Module], list[nn.Module]]:
    """(sınıflandırıcı başlık, ince ayarda açılacak son bloklar)."""
    if arch == "convnext_tiny":
        return [model.classifier], [model.features]       # tüm gövde (LayerNorm; BN yok)
    if arch == "efficientnet_b0":
        return [model.classifier], [model.features[5:]]   # MBConv 5-7 + son konv
    return [model.fc], [model.layer3, model.layer4]


def topk_correct(logits: torch.Tensor, targets: torch.Tensor, k: int) -> torch.Tensor:
    top = logits.topk(k, dim=1).indices
    return (top == targets.unsqueeze(1)).any(dim=1)


def macro_accuracy(y_true: np.ndarray, y_pred: np.ndarray, num_classes: int) -> float:
    """Sınıf bazlı doğrulukların (recall) ortalaması."""
    per_class = [np.mean(y_pred[y_true == c] == c) for c in range(num_classes) if np.any(y_true == c)]
    return float(np.mean(per_class))
