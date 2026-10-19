from __future__ import annotations

import numpy as np
import pandas as pd


def compensate_gain_scale(metadata: pd.DataFrame | None, n_rows: int | None = None) -> np.ndarray:
    if metadata is None or "compensate_gain_x1000000" not in metadata.columns:
        return np.ones(0 if n_rows is None else n_rows, dtype=np.float32)
    scale = pd.to_numeric(metadata["compensate_gain_x1000000"], errors="coerce").fillna(1_000_000).to_numpy(dtype=np.float32)
    scale = scale / 1_000_000.0
    if n_rows is not None:
        if len(scale) < n_rows:
            scale = np.pad(scale, (0, n_rows - len(scale)), constant_values=1.0)
        scale = scale[:n_rows]
    return scale


def gain_div_corrected(amplitude: np.ndarray, scale: np.ndarray, eps: float = 1e-9) -> np.ndarray:
    scale = np.asarray(scale, dtype=np.float32)
    if scale.size == 0:
        scale = np.ones(len(amplitude), dtype=np.float32)
    return np.asarray(amplitude, dtype=np.float32) / np.maximum(scale[:, None], eps)


def gain_mul_corrected(amplitude: np.ndarray, scale: np.ndarray) -> np.ndarray:
    scale = np.asarray(scale, dtype=np.float32)
    if scale.size == 0:
        scale = np.ones(len(amplitude), dtype=np.float32)
    return np.asarray(amplitude, dtype=np.float32) * scale[:, None]


def apply_gain_correction(amplitude: np.ndarray, scale: np.ndarray, preprocessing_type: str) -> np.ndarray:
    if preprocessing_type.startswith("gain_div"):
        return gain_div_corrected(amplitude, scale)
    if preprocessing_type.startswith("gain_mul"):
        return gain_mul_corrected(amplitude, scale)
    return np.asarray(amplitude, dtype=np.float32)

