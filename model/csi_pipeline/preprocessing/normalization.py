from __future__ import annotations

import numpy as np

BASE_PREPROCESSING = [
    "raw",
    "packet_mean_normalized",
    "window_mean_normalized",
    "packet_zscore_normalized",
    "window_zscore_normalized",
    "l2_normalized",
]

GAIN_PREPROCESSING = [
    "gain_div_corrected",
    "gain_mul_corrected",
    "gain_div_packet_mean",
    "gain_mul_packet_mean",
    "gain_div_l2",
    "gain_mul_l2",
]

ALL_PREPROCESSING = BASE_PREPROCESSING + GAIN_PREPROCESSING


def normalize_array(x: np.ndarray, normalization: str, eps: float = 1e-9) -> np.ndarray:
    y = np.asarray(x, dtype=np.float32)
    if normalization in {"raw", "gain_div_corrected", "gain_mul_corrected"}:
        return y.astype(np.float32, copy=True)
    if normalization in {"packet_mean_normalized", "gain_div_packet_mean", "gain_mul_packet_mean"}:
        denom = np.nanmean(y, axis=1, keepdims=True)
        return (y / np.maximum(denom, eps)).astype(np.float32)
    if normalization == "window_mean_normalized":
        return (y / max(float(np.nanmean(y)), eps)).astype(np.float32)
    if normalization == "packet_zscore_normalized":
        mean = np.nanmean(y, axis=1, keepdims=True)
        std = np.nanstd(y, axis=1, keepdims=True)
        return ((y - mean) / np.maximum(std, eps)).astype(np.float32)
    if normalization == "window_zscore_normalized":
        return ((y - float(np.nanmean(y))) / max(float(np.nanstd(y)), eps)).astype(np.float32)
    if normalization in {"l2_normalized", "gain_div_l2", "gain_mul_l2"}:
        denom = np.linalg.norm(y, axis=1, keepdims=True)
        return (y / np.maximum(denom, eps)).astype(np.float32)
    raise ValueError(f"unknown preprocessing type: {normalization}")

