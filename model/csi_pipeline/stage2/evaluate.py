"""Stage-2 static/moving evaluation metrics."""

from __future__ import annotations

import json

import numpy as np
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score, precision_score, recall_score


def evaluate_static_moving(y_true: np.ndarray, y_pred: np.ndarray, stage1_pass_rate: float | None = None, stage1_miss_count: int | None = None) -> dict:
    labels = [0, 1]
    cm = confusion_matrix(y_true, y_pred, labels=labels)
    recall = recall_score(y_true, y_pred, labels=labels, average=None, zero_division=0)
    precision = precision_score(y_true, y_pred, labels=labels, average=None, zero_division=0)
    out = {
        "macro_f1": float(f1_score(y_true, y_pred, labels=labels, average="macro", zero_division=0)),
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "static_recall": float(recall[0]),
        "moving_recall": float(recall[1]),
        "static_precision": float(precision[0]),
        "moving_precision": float(precision[1]),
        "confusion_matrix": json.dumps(cm.tolist()),
    }
    if stage1_pass_rate is not None:
        out["stage1_pass_rate"] = float(stage1_pass_rate)
    if stage1_miss_count is not None:
        out["stage1_miss_count"] = int(stage1_miss_count)
    return out
