from __future__ import annotations

import numpy as np

from csi_pipeline.preprocessing.gain_correction import apply_gain_correction
from csi_pipeline.preprocessing.normalization import normalize_array


def preprocess_block(amplitude: np.ndarray, gain_scale: np.ndarray | None, preprocessing_type: str) -> np.ndarray:
    scale = np.ones(len(amplitude), dtype=np.float32) if gain_scale is None else np.asarray(gain_scale, dtype=np.float32)
    corrected = apply_gain_correction(amplitude, scale, preprocessing_type)
    return normalize_array(corrected, preprocessing_type)


def preprocess_window_pair(
    baseline_window: np.ndarray,
    current_window: np.ndarray,
    baseline_gain: np.ndarray | None,
    current_gain: np.ndarray | None,
    preprocessing_type: str,
) -> tuple[np.ndarray, np.ndarray]:
    return (
        preprocess_block(baseline_window, baseline_gain, preprocessing_type),
        preprocess_block(current_window, current_gain, preprocessing_type),
    )

