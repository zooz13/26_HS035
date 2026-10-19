"""Stage-2 CNN training loop and checkpoint export."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time

import numpy as np
import torch
from sklearn.model_selection import train_test_split
from torch import nn
from torch.utils.data import DataLoader, TensorDataset

from csi_pipeline.data import TRAIN_ENVS
from csi_pipeline.stage2.dataset import build_stage2_training_arrays
from csi_pipeline.stage2.inference import predict
from csi_pipeline.stage2.model import TinyCnn


@dataclass
class TrainResult:
    model: nn.Module
    curve: list[dict[str, float]]
    best_epoch: int
    device_used: str


def macro_f1_np(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    from sklearn.metrics import f1_score

    labels = sorted(set(y_true.tolist()) | set(y_pred.tolist()))
    return float(f1_score(y_true, y_pred, labels=labels, average="macro", zero_division=0))


def train_cnn(
    x: np.ndarray,
    y: np.ndarray,
    train_idx: np.ndarray,
    val_idx: np.ndarray,
    n_classes: int = 2,
    epochs: int = 25,
    batch_size: int = 32,
    lr: float = 1e-3,
    weight_decay: float = 1e-4,
    seed: int = 42,
    device_name: str = "auto",
    class_weight: bool = True,
) -> TrainResult:
    torch.manual_seed(seed)
    np.random.seed(seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu") if device_name == "auto" else torch.device(device_name)
    model = TinyCnn(x.shape[2], n_classes).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=weight_decay)
    if class_weight:
        counts = np.bincount(y[train_idx], minlength=n_classes).astype(np.float32)
        weights = counts.sum() / np.maximum(counts, 1.0)
        weights = weights / weights.mean()
        loss_fn = nn.CrossEntropyLoss(weight=torch.tensor(weights, dtype=torch.float32, device=device))
    else:
        loss_fn = nn.CrossEntropyLoss()
    train_ds = TensorDataset(torch.from_numpy(x[train_idx]).float(), torch.from_numpy(y[train_idx]).long())
    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True)
    curve = []
    best_state = None
    best_f1 = -1.0
    best_epoch = 0
    patience = 6
    bad = 0
    for epoch in range(1, epochs + 1):
        model.train()
        losses = []
        for xb, yb in train_loader:
            xb = xb.to(device)
            yb = yb.to(device)
            opt.zero_grad(set_to_none=True)
            loss = loss_fn(model(xb), yb)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            losses.append(float(loss.item()))
        eval_idx = val_idx if len(val_idx) else train_idx
        val_pred = predict(model, x[eval_idx], batch_size=max(batch_size, 128))
        val_true = y[eval_idx]
        val_f1 = macro_f1_np(val_true, val_pred)
        with torch.no_grad():
            val_losses = []
            for start in range(0, len(eval_idx), max(batch_size, 128)):
                idx = eval_idx[start : start + max(batch_size, 128)]
                xb = torch.from_numpy(x[idx]).float().to(device)
                yb = torch.from_numpy(y[idx]).long().to(device)
                val_losses.append(float(loss_fn(model(xb), yb).item()))
        curve.append({"epoch": epoch, "train_loss": float(np.mean(losses)), "val_loss": float(np.mean(val_losses)), "val_macro_f1": val_f1})
        if val_f1 > best_f1:
            best_f1 = val_f1
            best_epoch = epoch
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
            bad = 0
        else:
            bad += 1
        if bad >= patience:
            break
    if best_state is not None:
        model.load_state_dict(best_state)
    return TrainResult(model=model, curve=curve, best_epoch=best_epoch, device_used=str(device))


def split_train_valid(y: np.ndarray, seed: int = 42) -> tuple[np.ndarray, np.ndarray]:
    idx = np.arange(len(y))
    try:
        return train_test_split(idx, test_size=0.2, random_state=seed, stratify=y)
    except ValueError:
        return train_test_split(idx, test_size=0.2, random_state=seed)


def train_stage2_small_cnn(
    x: np.ndarray,
    y: np.ndarray,
    train_idx: np.ndarray,
    valid_idx: np.ndarray,
    epochs: int = 25,
    seed: int = 42,
):
    return train_cnn(
        x,
        y,
        np.asarray(train_idx),
        np.asarray(valid_idx),
        n_classes=2,
        epochs=epochs,
        seed=seed,
        device_name="auto",
        class_weight=True,
    )


def save_stage2_checkpoint(
    path: str | Path,
    result: TrainResult,
    x_train: np.ndarray,
    preprocessing_type: str = "packet_mean_normalized",
    segment: str = "full_buffer",
    window_size: int = 128,
    stride: int = 64,
    seed: int = 42,
    extra: dict | None = None,
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint = {
        "state_dict": result.model.state_dict(),
        "model_class": "TinyCnn",
        "n_features": int(x_train.shape[2]),
        "n_classes": 2,
        "label_map": {"0": "static_person", "1": "moving_person"},
        "preprocessing_type": preprocessing_type,
        "segment": segment,
        "window_size": window_size,
        "stride": stride,
        "train_envs": TRAIN_ENVS,
        "seed": seed,
        "best_epoch": int(result.best_epoch),
        "curve": result.curve,
        "device_used_for_export_training": result.device_used,
    }
    if extra:
        checkpoint.update(extra)
    torch.save(checkpoint, path)
    return path


def train_and_save_stage2_checkpoint(
    output_path: str | Path,
    dataset_root: str | Path | None = None,
    preprocessing_type: str = "packet_mean_normalized",
    segment: str = "full_buffer",
    window_size: int = 128,
    stride: int = 64,
    epochs: int = 25,
    seed: int = 42,
) -> tuple[TrainResult, Path]:
    x_train, meta_train = build_stage2_training_arrays(dataset_root, preprocessing_type, segment, window_size, stride)
    y_train = meta_train["class_id"].to_numpy(dtype=int)
    train_idx, valid_idx = split_train_valid(y_train, seed)
    batch_size = 256 if len(x_train) >= 256 else max(16, min(128, len(x_train)))
    started = time.perf_counter()
    result = train_cnn(x_train, y_train, train_idx, valid_idx, n_classes=2, epochs=epochs, batch_size=batch_size, seed=seed, device_name="auto", class_weight=True)
    train_time_sec = time.perf_counter() - started
    ckpt = save_stage2_checkpoint(
        output_path,
        result,
        x_train,
        preprocessing_type,
        segment,
        window_size,
        stride,
        seed,
        {"train_time_sec": train_time_sec, "train_windows_total": int(len(x_train)), "num_train_windows": int(len(train_idx)), "num_valid_windows": int(len(valid_idx))},
    )
    return result, ckpt
