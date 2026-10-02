"""Uçak ailesi sınıflandırıcısı — transfer learning (ImageNet ön eğitimli CNN).

İki aşamalı eğitim:
  1) Gövde dondurulur, yalnızca son katman (başlık) eğitilir.
  2) Son bloklar açılır, düşük öğrenme oranıyla ince ayar (kosinüs azalma).
Kayıp: sınıf ağırlıklı cross-entropy (ters frekans).

Kullanım (proje kökünden):
  python -m src.ml.vision.train select --arch efficientnet_b0   # train'de eğit, val'de seç
  python -m src.ml.vision.train select --arch resnet50
  python -m src.ml.vision.train final    # seçilen mimari + epoch ile train+val'de yeniden eğit
Test seti burada hiç kullanılmaz (bkz. evaluate.py).

Her epoch sonunda models/vision/checkpoints/<çalıştırma>.pt kaydedilir (model, optimizer,
scheduler, scaler, geçmiş, RNG durumları). Yarıda kalan bir çalıştırma aynı komuta
--resume eklenerek son tamamlanan epoch'tan sürdürülür. Çalıştırma başarıyla bitince
checkpoint silinir.
"""
import argparse
import json
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import numpy as np  # noqa: E402
import torch  # noqa: E402
from torch import nn  # noqa: E402
from torch.utils.data import DataLoader  # noqa: E402

from src.ml.vision.common import (ARCHS, IMAGENET_MEAN, IMAGENET_STD, IMG_SIZE,  # noqa: E402
                                  MODEL_DIR, MODEL_PATH, build_model, class_weights,
                                  fgvc_dataset, head_and_last_blocks, macro_accuracy,
                                  topk_correct)

HPARAMS = dict(
    head_epochs=4,          # aşama 1
    ft_epochs=26,           # aşama 2 — kosinüs takviminin uzunluğu
    batch_size=32,
    lr_head=1e-3,           # aşama 1 başlık
    lr_ft_head=5e-4,        # aşama 2 başlık
    lr_ft_backbone=1e-4,    # aşama 2 açılan bloklar
    weight_decay=1e-4,
    label_smoothing=0.1,
    seed=42,
)
NUM_WORKERS = 6
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")
CHECKPOINT_DIR = MODEL_DIR / "checkpoints"


def selection_path(arch: str) -> Path:
    return MODEL_DIR / f"selection_{arch}.json"


def checkpoint_path(run_name: str) -> Path:
    return CHECKPOINT_DIR / f"{run_name}.pt"


def rng_state() -> dict:
    return {"python": random.getstate(), "numpy": np.random.get_state(),
            "torch": torch.get_rng_state(),
            "cuda": torch.cuda.get_rng_state_all() if torch.cuda.is_available() else None}


def set_rng_state(state: dict) -> None:
    random.setstate(state["python"])
    np.random.set_state(state["numpy"])
    torch.set_rng_state(state["torch"])
    if state["cuda"] is not None and torch.cuda.is_available():
        torch.cuda.set_rng_state_all(state["cuda"])


def save_checkpoint(path: Path, state: dict) -> None:
    """Önce geçici dosyaya yazar, sonra yerine koyar: yazım sırasında kesilirse
    önceki checkpoint bozulmaz."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    torch.save(state, tmp)
    os.replace(tmp, path)


def seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


def loader(ds, shuffle: bool) -> DataLoader:
    return DataLoader(ds, batch_size=HPARAMS["batch_size"], shuffle=shuffle,
                      num_workers=NUM_WORKERS, pin_memory=True, persistent_workers=NUM_WORKERS > 0,
                      drop_last=shuffle)


def set_trainable(model: nn.Module, modules: list[nn.Module]) -> None:
    for p in model.parameters():
        p.requires_grad = False
    for m in modules:
        for p in m.parameters():
            p.requires_grad = True


def train_mode(model: nn.Module) -> None:
    """Eğitim modu; ancak donmuş katmanlardaki BatchNorm istatistikleri sabit kalır."""
    model.train()
    for m in model.modules():
        if isinstance(m, nn.modules.batchnorm._BatchNorm) and \
                not any(p.requires_grad for p in m.parameters()):
            m.eval()


def run_epoch(model, dl, criterion, optimizer, scaler) -> float:
    train_mode(model)
    total, n = 0.0, 0
    for x, y in dl:
        x = x.to(DEVICE, non_blocking=True).to(memory_format=torch.channels_last)
        y = y.to(DEVICE, non_blocking=True)
        with torch.autocast(DEVICE.type, dtype=torch.float16, enabled=DEVICE.type == "cuda"):
            loss = criterion(model(x), y)
        optimizer.zero_grad(set_to_none=True)
        scaler.scale(loss).backward()
        scaler.step(optimizer)
        scaler.update()
        total += loss.item() * len(y)
        n += len(y)
    return total / n


@torch.no_grad()
def predict_logits(model, dl) -> tuple[torch.Tensor, torch.Tensor]:
    model.eval()
    logits, targets = [], []
    for x, y in dl:
        x = x.to(DEVICE, non_blocking=True).to(memory_format=torch.channels_last)
        with torch.autocast(DEVICE.type, dtype=torch.float16, enabled=DEVICE.type == "cuda"):
            logits.append(model(x).float().cpu())
        targets.append(y)
    return torch.cat(logits), torch.cat(targets)


def metrics(logits: torch.Tensor, targets: torch.Tensor, num_classes: int) -> dict:
    pred = logits.argmax(1)
    return {
        "top1": topk_correct(logits, targets, 1).float().mean().item(),
        "top3": topk_correct(logits, targets, 3).float().mean().item(),
        "macro_acc": macro_accuracy(targets.numpy(), pred.numpy(), num_classes),
    }


def setup_stage(model: nn.Module, head: list[nn.Module], blocks: list[nn.Module], stage: int):
    """Aşamaya göre eğitilebilir katmanları ayarlar; (optimizer, scheduler) döner."""
    if stage == 1:  # yalnızca başlık
        set_trainable(model, head)
        optimizer = torch.optim.AdamW([p for m in head for p in m.parameters()],
                                      lr=HPARAMS["lr_head"], weight_decay=HPARAMS["weight_decay"])
        return optimizer, None
    set_trainable(model, head + blocks)  # son bloklar açılır
    optimizer = torch.optim.AdamW([
        {"params": [p for m in head for p in m.parameters()], "lr": HPARAMS["lr_ft_head"]},
        {"params": [p for m in blocks for p in m.parameters()], "lr": HPARAMS["lr_ft_backbone"]},
    ], weight_decay=HPARAMS["weight_decay"])
    return optimizer, torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=HPARAMS["ft_epochs"])


def stage_of(epoch: int) -> int:
    return 1 if epoch <= HPARAMS["head_epochs"] else 2


def train(arch: str, train_ds, total_epochs: int, val_ds=None, run_name: str | None = None,
          resume: bool = False, run_meta: dict | None = None) -> tuple[nn.Module, list[dict]]:
    """total_epochs = aşama 1 + aşama 2 epoch sayısı. val_ds verilirse her epoch ölçülür.

    run_name verilirse her epoch sonunda checkpoint kaydedilir; resume=True ise varsa
    son checkpoint'ten devam edilir. run_meta, devam ederken ayarların aynı olduğunu
    doğrulamak için checkpoint'e yazılır.
    """
    seed_everything(HPARAMS["seed"])
    num_classes = len(train_ds.classes)
    model = build_model(arch, num_classes).to(DEVICE).to(memory_format=torch.channels_last)
    weights = class_weights(train_ds._labels, num_classes).to(DEVICE)
    criterion = nn.CrossEntropyLoss(weight=weights, label_smoothing=HPARAMS["label_smoothing"])
    scaler = torch.amp.GradScaler(enabled=DEVICE.type == "cuda")
    train_dl = loader(train_ds, shuffle=True)
    val_dl = loader(val_ds, shuffle=False) if val_ds is not None else None
    head, blocks = head_and_last_blocks(model, arch)
    history = []
    start_epoch = 1
    meta = {"arch": arch, "total_epochs": total_epochs, "hparams": HPARAMS,
            "n_train": len(train_ds), **(run_meta or {})}
    ckpt_path = checkpoint_path(run_name) if run_name else None

    if resume and ckpt_path is not None and ckpt_path.exists():
        ckpt = torch.load(ckpt_path, map_location=DEVICE, weights_only=False)
        if ckpt["meta"] != meta:
            diff = {k: (ckpt["meta"].get(k), v) for k, v in meta.items() if ckpt["meta"].get(k) != v}
            raise RuntimeError(f"Checkpoint ayarları uyuşmuyor (kayıtlı, şimdiki): {diff}. "
                               f"Baştan başlamak için --resume olmadan çalıştırın.")
        model.load_state_dict(ckpt["model"])
        optimizer, scheduler = setup_stage(model, head, blocks, stage_of(ckpt["epoch"]))
        optimizer.load_state_dict(ckpt["optimizer"])
        if scheduler is not None:
            scheduler.load_state_dict(ckpt["scheduler"])
        scaler.load_state_dict(ckpt["scaler"])
        history = ckpt["history"]
        set_rng_state(ckpt["rng"])
        start_epoch = ckpt["epoch"] + 1
        print(f"  Checkpoint'ten devam: {ckpt['epoch']}/{total_epochs} epoch tamamlanmış "
              f"({ckpt_path.name})", flush=True)
    elif resume:
        print(f"  Checkpoint bulunamadı ({ckpt_path}); baştan başlanıyor.", flush=True)

    for epoch in range(start_epoch, total_epochs + 1):
        if epoch == 1 or epoch == HPARAMS["head_epochs"] + 1:  # aşama başlangıcı
            optimizer, scheduler = setup_stage(model, head, blocks, stage_of(epoch))

        t0 = time.perf_counter()
        loss = run_epoch(model, train_dl, criterion, optimizer, scaler)
        if scheduler is not None:
            scheduler.step()
        row = {"epoch": epoch, "stage": stage_of(epoch), "train_loss": round(loss, 4)}
        if val_dl is not None:
            logits, targets = predict_logits(model, val_dl)
            row["val_loss"] = round(nn.functional.cross_entropy(logits, targets).item(), 4)
            row.update({f"val_{k}": round(v, 4) for k, v in metrics(logits, targets, num_classes).items()})
        row["sec"] = round(time.perf_counter() - t0, 1)
        history.append(row)
        print("  " + " | ".join(f"{k}={v}" for k, v in row.items()), flush=True)
        if ckpt_path is not None:
            save_checkpoint(ckpt_path, {
                "epoch": epoch, "meta": meta, "model": model.state_dict(),
                "optimizer": optimizer.state_dict(),
                "scheduler": scheduler.state_dict() if scheduler is not None else None,
                "scaler": scaler.state_dict(), "history": history, "rng": rng_state(),
            })
    return model, history


def clear_checkpoint(run_name: str) -> None:
    """Çalıştırmanın asıl çıktısı kaydedildikten sonra çağrılır."""
    checkpoint_path(run_name).unlink(missing_ok=True)


def cmd_select(arch: str, resume: bool = False) -> None:
    train_ds, val_ds = fgvc_dataset("train", train=True), fgvc_dataset("val", train=False)
    total = HPARAMS["head_epochs"] + HPARAMS["ft_epochs"]
    run_name = f"select_{arch}"
    print(f"[select] {arch} | train={len(train_ds)} val={len(val_ds)} | {total} epoch | {DEVICE}")
    _, history = train(arch, train_ds, total, val_ds, run_name=run_name, resume=resume,
                       run_meta={"mode": "select", "img_size": IMG_SIZE})
    best = max(history, key=lambda r: (r["val_macro_acc"], r["val_top1"]))
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    selection_path(arch).write_text(json.dumps(
        {"arch": arch, "hparams": HPARAMS, "img_size": IMG_SIZE, "best": best, "history": history},
        indent=2), encoding="utf-8")
    clear_checkpoint(run_name)
    print(f"En iyi epoch: {best['epoch']} | val top1={best['val_top1']:.4f} "
          f"top3={best['val_top3']:.4f} macro={best['val_macro_acc']:.4f}")


def cmd_final(resume: bool = False) -> None:
    selections = [json.loads(selection_path(a).read_text(encoding="utf-8"))
                  for a in ARCHS if selection_path(a).exists()]
    if not selections:
        sys.exit("Önce `select` çalıştırın.")
    chosen = max(selections, key=lambda s: (s["best"]["val_macro_acc"], s["best"]["val_top1"]))
    arch, epochs = chosen["arch"], chosen["best"]["epoch"]
    if epochs <= HPARAMS["head_epochs"]:
        sys.exit("En iyi epoch aşama 1'de; ince ayar ayarlarını gözden geçirin.")
    trainval = fgvc_dataset("trainval", train=True)
    print(f"[final] {arch} | train+val={len(trainval)} | {epochs} epoch "
          f"(val seçimi: macro={chosen['best']['val_macro_acc']:.4f}) | {DEVICE}")
    model, history = train(arch, trainval, epochs, run_name="final", resume=resume,
                           run_meta={"mode": "final", "img_size": IMG_SIZE,
                                     "val_selection": chosen["best"]})

    version = datetime.now(timezone.utc).strftime(f"{arch}-%Y%m%d-%H%M%S")
    torch.save({
        "version": version, "arch": arch, "classes": trainval.classes,
        "state_dict": model.state_dict(), "img_size": IMG_SIZE,
        "mean": IMAGENET_MEAN, "std": IMAGENET_STD, "epochs": epochs,
        "trained_on": "trainval", "hparams": HPARAMS, "history": history,
        "val_selection": chosen["best"],
    }, MODEL_PATH)
    clear_checkpoint("final")
    print(f"Kaydedildi: {MODEL_PATH} ({version})")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    sel = sub.add_parser("select", help="train'de eğit, val'de epoch/ayar seç")
    sel.add_argument("--arch", choices=ARCHS, required=True)
    fin = sub.add_parser("final", help="seçilen ayarlarla train+val'de yeniden eğit")
    for p in (sel, fin):
        p.add_argument("--resume", action="store_true",
                       help="yarıda kalan çalıştırmayı son checkpoint'ten sürdür")
    args = parser.parse_args()
    cmd_select(args.arch, args.resume) if args.cmd == "select" else cmd_final(args.resume)


if __name__ == "__main__":
    main()
