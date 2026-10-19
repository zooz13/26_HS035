"""Stage-1 baseline construction and shared CSI window preprocessing helpers."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from csi_pipeline.preprocessing.preprocess_pipeline import preprocess_block
from csi_pipeline.preprocessing.windowing import window_starts


SEGMENTS = {
    "full_buffer": (0, 192),
    "official_LLTF": (0, 64),
    "official_HT-LTF": (64, 192),
}


def segment_amp(session: dict, segment: str) -> np.ndarray:
    start, end = SEGMENTS[segment]
    return session["amplitude"][:, start:end]


def gain_scale(session: dict, start: int, end: int) -> np.ndarray:
    meta = session.get("metadata", pd.DataFrame())
    if "compensate_gain_scale" not in meta.columns:
        return np.ones(max(0, end - start), dtype=np.float32)
    vals = meta["compensate_gain_scale"].iloc[start:end].to_numpy(dtype=np.float32)
    return np.where(np.isfinite(vals) & (np.abs(vals) > 1e-9), vals, 1.0)


def preprocess_session_segment(session: dict, segment: str, preprocessing_type: str, row_start: int = 0, row_end: int | None = None) -> np.ndarray:
    amp = segment_amp(session, segment)
    if row_end is None:
        row_end = len(amp)
    return preprocess_block(amp[row_start:row_end], gain_scale(session, row_start, row_end), preprocessing_type)


def window_motion_features(win: np.ndarray) -> dict[str, float]:
    temporal_std = np.nanstd(win, axis=0)
    diff = np.diff(win, axis=0)
    abs_diff = np.abs(diff)
    per_t = np.nanmean(abs_diff, axis=1) if abs_diff.size else np.array([0.0])
    top = np.sort(per_t)[-10:] if per_t.size else np.array([0.0])
    return {
        "temporal_std_mean": float(np.nanmean(temporal_std)),
        "temporal_std_p95": float(np.nanpercentile(temporal_std, 95)),
        "diff_abs_mean": float(np.nanmean(abs_diff)) if abs_diff.size else 0.0,
        "diff_abs_p95": float(np.nanpercentile(abs_diff, 95)) if abs_diff.size else 0.0,
        "top10_diff_mean": float(np.nanmean(top)),
        "diff_energy_sum": float(np.nansum(np.nanmean(diff**2, axis=1))) if diff.size else 0.0,
    }


def robust_stats(values: np.ndarray, minimum_scale_ratio: float = 0.05) -> dict[str, np.ndarray]:
    med = np.nanmedian(values, axis=0)
    q25 = np.nanpercentile(values, 25, axis=0)
    q75 = np.nanpercentile(values, 75, axis=0)
    iqr = q75 - q25
    mad = np.nanmedian(np.abs(values - med[None, :]), axis=0)
    global_scale = max(float(np.nanmedian(np.abs(values))) * minimum_scale_ratio, 1e-3)
    robust_scale = np.maximum.reduce([mad * 1.4826, iqr / 1.349, np.full_like(med, global_scale)])
    return {"median": med, "iqr": iqr, "mad": mad, "robust_scale": robust_scale}


def calibration_motion_scale(cal_amp: np.ndarray, window_size: int, stride: int) -> dict[str, float]:
    rows = [window_motion_features(cal_amp[start : start + window_size]) for start in window_starts(len(cal_amp), window_size, stride)]
    if not rows:
        rows = [window_motion_features(cal_amp)]
    df = pd.DataFrame(rows)
    return {
        "diff_abs_p95_scale": max(float(df["diff_abs_mean"].quantile(0.95)), 1e-6),
        "temporal_std_p95_scale": max(float(df["temporal_std_mean"].quantile(0.95)), 1e-6),
    }


def build_baseline(cal_amp: np.ndarray, window_size: int, stride: int, minimum_scale_ratio: float = 0.05) -> dict:
    baseline = {**robust_stats(cal_amp, minimum_scale_ratio), **calibration_motion_scale(cal_amp, window_size, stride)}
    baseline["mean"] = np.nanmean(cal_amp, axis=0)
    baseline["baseline_amp_mean"] = baseline["mean"]
    baseline["baseline_amp_median"] = baseline["median"]
    baseline["baseline_amp_std"] = np.nanstd(cal_amp, axis=0)
    shape = baseline["mean"]
    baseline["baseline_shape"] = (shape - np.nanmean(shape)) / max(float(np.nanstd(shape)), 1e-6)
    return baseline


def count_windows(n_rows: int, window_size: int, stride: int) -> int:
    if n_rows < window_size:
        return 0
    return int(math.floor((n_rows - window_size) / stride) + 1)


def normalized_calibration_stack(parts: list[tuple[dict, int]], segment: str, preprocessing_type: str, window_size: int, stride: int) -> np.ndarray:
    chunks = []
    for session, rows in parts:
        n = min(rows, len(segment_amp(session, segment)))
        for start in window_starts(n, window_size, stride):
            chunks.append(preprocess_session_segment(session, segment, preprocessing_type, start, start + window_size))
        if not chunks and n:
            chunks.append(preprocess_session_segment(session, segment, preprocessing_type, 0, n))
    if not chunks:
        raise RuntimeError("empty calibration stack")
    return np.vstack(chunks)


def build_normalized_baseline(parts: list[tuple[dict, int]], segment: str, preprocessing_type: str, window_size: int, stride: int) -> tuple[dict, int]:
    stack = normalized_calibration_stack(parts, segment, preprocessing_type, window_size, stride)
    baseline = build_baseline(stack, window_size, stride)
    n_windows = sum(count_windows(rows, window_size, stride) for _, rows in parts)
    return baseline, max(1, int(n_windows))
