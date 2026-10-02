"""Fotoğraf sınıflandırma — eğitim, değerlendirme ve tahminin paylaştığı parçalar."""
import numpy as np
import torch
from torch import nn
from torchvision import models, transforms
from torchvision.datasets import FGVCAircraft

import config

IMG_SIZE = 320
IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)
FGVC_BANNER_PX = 20  # FGVC görüntülerinin altındaki telif şeridi (yalnızca veri setinde)

MODEL_DIR = config.MODELS_DIR / "vision"
MODEL_PATH = MODEL_DIR / "aircraft_classifier.pt"

ARCHS = ("efficientnet_b0", "resnet50")


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


def fgvc_dataset(split: str, train: bool) -> FGVCAircraft:
    """split: train, val, trainval, test. Aile (family) düzeyi, 70 sınıf."""
    return FGVCAircraft(root=str(config.FGVC_DIR), split=split, annotation_level="family",
                        transform=train_transform() if train else eval_transform(), download=False)


def class_weights(labels, num_classes: int) -> torch.Tensor:
    """Ters frekans ağırlıkları, ortalaması 1: w_c = N / (K * n_c)."""
    counts = np.bincount(np.asarray(labels), minlength=num_classes).astype(np.float64)
    weights = len(labels) / (num_classes * np.maximum(counts, 1))
    return torch.tensor(weights, dtype=torch.float32)


def build_model(arch: str, num_classes: int, pretrained: bool = True) -> nn.Module:
    if arch == "efficientnet_b0":
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
