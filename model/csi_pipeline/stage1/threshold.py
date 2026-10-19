"""Stage-1 metric and threshold selection policy."""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import balanced_accuracy_score, confusion_matrix, f1_score, precision_recall_fscore_support


def metric_dict(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float | int]:
    cm = confusion_matrix(y_true, y_pred, labels=[0, 1])
    precision, recall, f1, _ = precision_recall_fscore_support(y_true, y_pred, labels=[0, 1], zero_division=0)
    empty_recall = float(recall[0])
    occupied_recall = float(recall[1])
    return {
        "accuracy": float((y_true == y_pred).mean()) if len(y_true) else np.nan,
        "macro_f1": float(f1_score(y_true, y_pred, average="macro", zero_division=0)) if len(y_true) else np.nan,
        "balanced_accuracy": float(balanced_accuracy_score(y_true, y_pred)) if len(np.unique(y_true)) > 1 else np.nan,
        "empty_precision": float(precision[0]),
        "empty_recall": empty_recall,
        "empty_f1": float(f1[0]),
        "occupied_precision": float(precision[1]),
        "occupied_recall": occupied_recall,
        "occupied_f1": float(f1[1]),
        "occupied_miss_rate": float(1 - occupied_recall),
        "empty_false_alarm_rate": float(1 - empty_recall),
        "tn": int(cm[0, 0]),
        "fp": int(cm[0, 1]),
        "fn": int(cm[1, 0]),
        "tp": int(cm[1, 1]),
    }


def threshold_candidates(values: np.ndarray) -> np.ndarray:
    values = values[np.isfinite(values)]
    if values.size == 0:
        return np.array([0.5])
    return np.unique(np.quantile(values, np.linspace(0.005, 0.995, 220)))


def choose_threshold(train_df: pd.DataFrame, score_col: str, policy: str = "balanced_macro") -> tuple[float, dict]:
    values = train_df[score_col].to_numpy(dtype=float)
    y = train_df["true_binary"].to_numpy(dtype=int)
    best = None
    for thr in threshold_candidates(values):
        pred = (values >= thr).astype(int)
        metrics = metric_dict(y, pred)
        if policy != "balanced_macro":
            raise ValueError(f"unsupported threshold policy: {policy}")
        key = (-metrics["macro_f1"], metrics["empty_false_alarm_rate"], metrics["occupied_miss_rate"])
        if best is None or key < best[0]:
            best = (key, float(thr), metrics)
    out = dict(best[2])
    out["threshold_policy"] = policy
    out["threshold_selection_reason"] = "selected_max_train_macro_f1"
    return best[1], out
