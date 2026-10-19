"""Stage-1 evaluation entry points and binary metric helpers."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score, precision_recall_fscore_support

from csi_pipeline.data import TARGET_ENV, TARGET_LABELS, default_dataset_root, load_env, load_train_envs
from csi_pipeline.stage1.calibration import build_stage1_test_table
from csi_pipeline.stage1.inference import apply_detector
from csi_pipeline.stage1.threshold import metric_dict
from csi_pipeline.stage1.train import fit_stage1_detector


def binary_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float | int]:
    y_true = np.asarray(y_true, dtype=int)
    y_pred = np.asarray(y_pred, dtype=int)
    cm = confusion_matrix(y_true, y_pred, labels=[0, 1])
    precision, recall, f1, _ = precision_recall_fscore_support(y_true, y_pred, labels=[0, 1], zero_division=0)
    return {
        "accuracy": float(accuracy_score(y_true, y_pred)) if len(y_true) else np.nan,
        "macro_f1": float(f1_score(y_true, y_pred, labels=[0, 1], average="macro", zero_division=0)) if len(y_true) else np.nan,
        "empty_precision": float(precision[0]),
        "empty_recall": float(recall[0]),
        "empty_f1": float(f1[0]),
        "occupied_precision": float(precision[1]),
        "occupied_recall": float(recall[1]),
        "occupied_f1": float(f1[1]),
        "false_alarm": float(1.0 - recall[0]),
        "occupied_miss": float(1.0 - recall[1]),
        "tn": int(cm[0, 0]),
        "fp": int(cm[0, 1]),
        "fn": int(cm[1, 0]),
        "tp": int(cm[1, 1]),
    }


def evaluate_threshold(scores: np.ndarray, labels: np.ndarray, threshold: float) -> dict:
    preds = (np.asarray(scores, dtype=float) >= float(threshold)).astype(int)
    return binary_metrics(np.asarray(labels, dtype=int), preds)


def evaluate_room4_2026_05_27_stage1(
    dataset_root: str | Path | None = None,
    preprocessing_type: str = "gain_mul_corrected",
    method: str = "guided 20%",
    segment: str = "full_buffer",
    feature_set: str = "baseline_plus_shape",
    detector: str = "robust_max_shape",
    threshold_policy: str = "balanced_macro",
    window_size: int = 128,
    stride: int = 64,
    seed: int = 42,
):
    root = default_dataset_root() if dataset_root is None else Path(dataset_root)
    train_envs = load_train_envs(root)
    target_env = load_env(root, TARGET_ENV, TARGET_LABELS)
    fitted = fit_stage1_detector(train_envs, preprocessing_type, segment, feature_set, detector, threshold_policy, 20.0, window_size, stride, seed)
    test_df, cal_windows, notes = build_stage1_test_table(target_env, preprocessing_type, method, segment, 20.0, 20.0, window_size, stride)
    pred = apply_detector(test_df, fitted)
    metrics = metric_dict(pred["true_binary"].to_numpy(dtype=int), pred["pred_binary"].to_numpy(dtype=int))
    return {
        "metrics": metrics,
        "predictions": pred,
        "num_calibration_windows": cal_windows,
        "notes": notes,
        "detector": fitted,
    }


def evaluate_room4_stage1_default(dataset_root: str | Path | None = None, preprocessing_type: str = "gain_mul_corrected", method: str = "guided 20%") -> dict:
    root = default_dataset_root() if dataset_root is None else Path(dataset_root)
    train_envs = load_train_envs(root)
    target_env = load_env(root, TARGET_ENV, TARGET_LABELS)
    detector = fit_stage1_detector(train_envs, preprocessing_type)
    test_df, cal_windows, notes = build_stage1_test_table(target_env, preprocessing_type, method)
    pred = apply_detector(test_df, detector)
    metrics = metric_dict(pred["true_binary"].to_numpy(dtype=int), pred["pred_binary"].to_numpy(dtype=int))
    return {"metrics": metrics, "predictions": pred, "detector": detector, "num_calibration_windows": cal_windows, "notes": notes}
