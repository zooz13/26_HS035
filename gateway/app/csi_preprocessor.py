"""Gateway CSI preprocessing based on the model-server reference contract."""

from __future__ import annotations

import ast
import math
import re
from typing import Any

import numpy as np

from app.baseline_state import BaselineState


WINDOW_SIZE = 128
STRIDE = 64
STAGE1_FEATURE_VERSION = "stage1_feature_v1"
STAGE2_FEATURE_VERSION = "stage2_feature_v1"
DATA_TYPE_STAGE1 = "stage1_feature_vector"
PREPROCESS_VERSION = "preprocess_v1"
NORMALIZATION_VERSION = "packet_mean_normalized"
STAGE2_NORMALIZATION = "packet_mean_normalized"
DEFAULT_SEGMENT = "full_buffer"

SEGMENTS = {
    "full_buffer": (0, 192),
    "official_LLTF": (0, 64),
    "official_HT-LTF": (64, 192),
}

BASE_PREPROCESSING = {
    "raw",
    "packet_mean_normalized",
    "window_mean_normalized",
    "packet_zscore_normalized",
    "window_zscore_normalized",
    "l2_normalized",
}

GAIN_PREPROCESSING = {
    "gain_div_corrected",
    "gain_mul_corrected",
    "gain_div_packet_mean",
    "gain_mul_packet_mean",
    "gain_div_l2",
    "gain_mul_l2",
}


def parse_csi_data(data_value: object) -> np.ndarray:
    if isinstance(data_value, np.ndarray):
        return data_value.astype(np.float32)
    if isinstance(data_value, (list, tuple)):
        return np.asarray(data_value, dtype=np.float32)
    if data_value is None or (
        isinstance(data_value, float) and np.isnan(data_value)
    ):
        return np.array([], dtype=np.float32)
    text = str(data_value).strip()
    if text.startswith("[") and text.endswith("]"):
        fast = np.fromstring(text[1:-1], sep=",", dtype=np.float32)
        if fast.size:
            return fast
    try:
        parsed = ast.literal_eval(text)
        if isinstance(parsed, (list, tuple)):
            return np.asarray(parsed, dtype=np.float32)
    except Exception:
        pass
    return np.asarray(
        [
            float(value)
            for value in re.findall(r"[-+]?\d+(?:\.\d+)?", text)
        ],
        dtype=np.float32,
    )


def raw_to_complex(raw: np.ndarray) -> np.ndarray:
    arr = np.asarray(raw, dtype=np.float32)
    if arr.shape[-1] % 2:
        raise ValueError(f"raw width must be even, got {arr.shape[-1]}")
    imag = arr[..., 0::2]
    real = arr[..., 1::2]
    return real + 1j * imag


def iq_to_amplitude(iq_array: np.ndarray) -> np.ndarray:
    arr = np.asarray(iq_array, dtype=np.float32)
    if arr.ndim == 1:
        if arr.size % 2:
            raise ValueError(f"I/Q length must be even, got {arr.size}")
        imag = arr[0::2]
        real = arr[1::2]
        return np.sqrt(real * real + imag * imag).astype(np.float32)
    if arr.shape[-1] % 2:
        raise ValueError(f"I/Q width must be even, got {arr.shape[-1]}")
    imag = arr[..., 0::2]
    real = arr[..., 1::2]
    return np.sqrt(real * real + imag * imag).astype(np.float32)


def parse_raw_csi_packet(raw_packet: object) -> np.ndarray:
    value = (
        raw_packet.get("data")
        if isinstance(raw_packet, dict) and "data" in raw_packet
        else raw_packet
    )
    arr = parse_csi_data(value)
    if arr.size == 384:
        amp = iq_to_amplitude(arr)
    elif arr.size == 192:
        amp = arr.astype(np.float32)
    else:
        raise ValueError(
            "raw CSI packet must decode to 384 I/Q values or "
            f"192 amplitudes, got {arr.size}"
        )
    if amp.shape[0] != 192:
        raise ValueError(f"expected amplitude length 192, got {amp.shape[0]}")
    return np.nan_to_num(
        amp.astype(np.float32),
        nan=0.0,
        posinf=0.0,
        neginf=0.0,
    )


def gain_div_corrected(
    amplitude: np.ndarray,
    scale: np.ndarray,
    eps: float = 1e-9,
) -> np.ndarray:
    scale = np.asarray(scale, dtype=np.float32)
    if scale.size == 0:
        scale = np.ones(len(amplitude), dtype=np.float32)
    return np.asarray(amplitude, dtype=np.float32) / np.maximum(
        scale[:, None], eps
    )


def gain_mul_corrected(
    amplitude: np.ndarray,
    scale: np.ndarray,
) -> np.ndarray:
    scale = np.asarray(scale, dtype=np.float32)
    if scale.size == 0:
        scale = np.ones(len(amplitude), dtype=np.float32)
    return np.asarray(amplitude, dtype=np.float32) * scale[:, None]


def apply_gain_correction(
    amplitude: np.ndarray,
    scale: np.ndarray,
    preprocessing_type: str,
) -> np.ndarray:
    if preprocessing_type.startswith("gain_div"):
        return gain_div_corrected(amplitude, scale)
    if preprocessing_type.startswith("gain_mul"):
        return gain_mul_corrected(amplitude, scale)
    return np.asarray(amplitude, dtype=np.float32)


def normalize_array(
    x: np.ndarray,
    normalization: str,
    eps: float = 1e-9,
) -> np.ndarray:
    y = np.asarray(x, dtype=np.float32)
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        if normalization in {"raw", "gain_div_corrected", "gain_mul_corrected"}:
            result = y.astype(np.float32, copy=True)
        elif normalization in {
            "packet_mean_normalized",
            "gain_div_packet_mean",
            "gain_mul_packet_mean",
        }:
            denom = np.nanmean(y, axis=1, keepdims=True)
            result = (y / np.maximum(denom, eps)).astype(np.float32)
        elif normalization == "window_mean_normalized":
            result = (y / max(float(np.nanmean(y)), eps)).astype(np.float32)
        elif normalization == "packet_zscore_normalized":
            mean = np.nanmean(y, axis=1, keepdims=True)
            std = np.nanstd(y, axis=1, keepdims=True)
            result = ((y - mean) / np.maximum(std, eps)).astype(np.float32)
        elif normalization == "window_zscore_normalized":
            result = (
                (y - float(np.nanmean(y))) / max(float(np.nanstd(y)), eps)
            ).astype(np.float32)
        elif normalization in {"l2_normalized", "gain_div_l2", "gain_mul_l2"}:
            denom = np.linalg.norm(y, axis=1, keepdims=True)
            result = (y / np.maximum(denom, eps)).astype(np.float32)
        else:
            raise ValueError(f"unknown preprocessing type: {normalization}")
    return np.nan_to_num(
        result, nan=0.0, posinf=0.0, neginf=0.0
    ).astype(np.float32)


def preprocess_block(
    amplitude: np.ndarray,
    gain_scale: np.ndarray | None,
    preprocessing_type: str,
) -> np.ndarray:
    scale = (
        np.ones(len(amplitude), dtype=np.float32)
        if gain_scale is None
        else np.asarray(gain_scale, dtype=np.float32)
    )
    corrected = apply_gain_correction(amplitude, scale, preprocessing_type)
    return normalize_array(corrected, preprocessing_type)


def segment_amp(amplitude: np.ndarray, segment: str) -> np.ndarray:
    if segment not in SEGMENTS:
        raise ValueError(
            f"unknown segment {segment!r}; expected one of {sorted(SEGMENTS)}"
        )
    start, end = SEGMENTS[segment]
    return np.asarray(amplitude, dtype=np.float32)[:, start:end]


def preprocess_amplitude_segment(
    amplitude: np.ndarray,
    segment: str = DEFAULT_SEGMENT,
    preprocessing_type: str = STAGE2_NORMALIZATION,
    row_start: int = 0,
    row_end: int | None = None,
    gain_scale: np.ndarray | None = None,
) -> np.ndarray:
    amp = segment_amp(amplitude, segment)
    if row_end is None:
        row_end = len(amp)
    row_gain = (
        None
        if gain_scale is None
        else np.asarray(gain_scale, dtype=np.float32)[row_start:row_end]
    )
    return preprocess_block(
        amp[row_start:row_end],
        row_gain,
        preprocessing_type,
    )


def window_motion_features(win: np.ndarray) -> dict[str, float]:
    temporal_std = np.nanstd(win, axis=0)
    diff = np.diff(win, axis=0)
    abs_diff = np.abs(diff)
    per_t = (
        np.nanmean(abs_diff, axis=1)
        if abs_diff.size
        else np.array([0.0])
    )
    top = np.sort(per_t)[-10:] if per_t.size else np.array([0.0])
    return {
        "temporal_std_mean": float(np.nanmean(temporal_std)),
        "temporal_std_p95": float(np.nanpercentile(temporal_std, 95)),
        "diff_abs_mean": (
            float(np.nanmean(abs_diff)) if abs_diff.size else 0.0
        ),
        "diff_abs_p95": (
            float(np.nanpercentile(abs_diff, 95))
            if abs_diff.size
            else 0.0
        ),
        "top10_diff_mean": float(np.nanmean(top)),
        "diff_energy_sum": (
            float(np.nansum(np.nanmean(diff**2, axis=1)))
            if diff.size
            else 0.0
        ),
    }


def window_starts(
    n_rows: int,
    window_size: int = WINDOW_SIZE,
    stride: int = STRIDE,
) -> list[int]:
    if n_rows < window_size:
        return []
    return list(range(0, n_rows - window_size + 1, stride))


def count_windows(
    n_rows: int,
    window_size: int = WINDOW_SIZE,
    stride: int = STRIDE,
) -> int:
    return len(window_starts(n_rows, window_size, stride))


def robust_stats(
    values: np.ndarray,
    minimum_scale_ratio: float = 0.05,
) -> dict[str, np.ndarray]:
    med = np.nanmedian(values, axis=0)
    q25 = np.nanpercentile(values, 25, axis=0)
    q75 = np.nanpercentile(values, 75, axis=0)
    iqr = q75 - q25
    mad = np.nanmedian(np.abs(values - med[None, :]), axis=0)
    global_scale = max(
        float(np.nanmedian(np.abs(values))) * minimum_scale_ratio,
        1e-3,
    )
    robust_scale = np.maximum.reduce(
        [
            mad * 1.4826,
            iqr / 1.349,
            np.full_like(med, global_scale),
        ]
    )
    return {
        "median": med,
        "robust_scale": robust_scale,
    }


def calibration_motion_scale(
    amplitude: np.ndarray,
    window_size: int = WINDOW_SIZE,
    stride: int = STRIDE,
) -> dict[str, float]:
    rows = [
        window_motion_features(amplitude[start : start + window_size])
        for start in window_starts(len(amplitude), window_size, stride)
    ]
    if not rows:
        rows = [window_motion_features(amplitude)]
    diff_values = np.asarray(
        [row["diff_abs_mean"] for row in rows],
        dtype=np.float32,
    )
    std_values = np.asarray(
        [row["temporal_std_mean"] for row in rows],
        dtype=np.float32,
    )
    return {
        "diff_abs_p95_scale": max(
            float(np.quantile(diff_values, 0.95)),
            1e-6,
        ),
        "temporal_std_p95_scale": max(
            float(np.quantile(std_values, 0.95)),
            1e-6,
        ),
    }


def build_baseline_state(
    amplitude: np.ndarray,
    *,
    baseline_id: str,
    duration_sec: float,
    window_duration_sec: float,
    segment: str = DEFAULT_SEGMENT,
    preprocessing_type: str = NORMALIZATION_VERSION,
    window_size: int = WINDOW_SIZE,
    stride: int = STRIDE,
) -> BaselineState:
    values = np.asarray(amplitude, dtype=np.float32)
    if values.ndim != 2 or values.shape[1] != 192:
        raise ValueError(
            f"baseline amplitude must have shape [N,192], got {list(values.shape)}"
        )
    if len(values) < window_size:
        raise ValueError(
            f"baseline needs at least {window_size} packets, got {len(values)}"
        )
    normalized = preprocess_amplitude_segment(
        values,
        segment=segment,
        preprocessing_type=preprocessing_type,
    )
    stats = robust_stats(normalized)
    motion_scale = calibration_motion_scale(
        normalized,
        window_size,
        stride,
    )
    mean = np.nanmean(normalized, axis=0)
    shape = (mean - np.nanmean(mean)) / max(float(np.nanstd(mean)), 1e-6)
    return BaselineState(
        baseline_id=baseline_id,
        segment=segment,
        preprocessing_type=preprocessing_type,
        window_size=window_size,
        stride=stride,
        median=stats["median"].astype(np.float32),
        robust_scale=stats["robust_scale"].astype(np.float32),
        diff_abs_p95_scale=motion_scale["diff_abs_p95_scale"],
        temporal_std_p95_scale=motion_scale["temporal_std_p95_scale"],
        baseline_amp_mean=mean.astype(np.float32),
        baseline_amp_median=np.nanmedian(normalized, axis=0).astype(np.float32),
        baseline_amp_std=np.nanstd(normalized, axis=0).astype(np.float32),
        baseline_shape=shape.astype(np.float32),
        num_windows=count_windows(len(normalized), window_size, stride),
        duration_sec=float(duration_sec),
        window_duration_sec=float(window_duration_sec),
    )


def baseline_score_summary(
    amplitude: np.ndarray,
    baseline_state: BaselineState,
) -> dict[str, float]:
    scores: list[float] = []
    for start in window_starts(
        len(amplitude),
        baseline_state.window_size,
        baseline_state.stride,
    ):
        features = stage1_feature_vector(
            amplitude[
                start : start + baseline_state.window_size
            ],
            baseline_state,
        )
        scores.append(features["robust_max_shape_score"])
    if not scores:
        raise ValueError("baseline has no complete scoring windows")
    values = np.asarray(scores, dtype=np.float32)
    return {
        "median_score": float(np.quantile(values, 0.50)),
        "p90_score": float(np.quantile(values, 0.90)),
        "p95_score": float(np.quantile(values, 0.95)),
        "p99_score": float(np.quantile(values, 0.99)),
        "valid_packet_count": float(len(amplitude)),
    }


def cosine_distance(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom <= 1e-12:
        return 0.0
    return float(1.0 - np.dot(a, b) / denom)


def safe_corr(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 2 or len(b) < 2:
        return 0.0
    if not np.isfinite(a).all() or not np.isfinite(b).all():
        return 0.0
    if float(np.nanstd(a)) <= 1e-9 or float(np.nanstd(b)) <= 1e-9:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def robust_max_shape_score(features: dict[str, float]) -> float:
    return float(
        max(
            features["static_score"],
            features["motion_score"],
            features["shape_score"],
        )
    )


def baseline_plus_shape_features(
    win: np.ndarray,
    baseline: dict[str, Any],
    clip: float = 20.0,
) -> dict[str, float]:
    med = np.nanmedian(win, axis=0)
    mean = np.nanmean(win, axis=0)
    z_abs = np.abs(med - baseline["median"]) / baseline["robust_scale"]
    z_abs = np.clip(z_abs, 0, clip)
    motion = window_motion_features(win)
    static_score = float(np.nanpercentile(z_abs, 90))
    motion_score = max(
        motion["diff_abs_mean"] / float(baseline["diff_abs_p95_scale"]),
        motion["temporal_std_mean"]
        / float(baseline["temporal_std_p95_scale"]),
    )
    top20_n = max(1, int(math.ceil(len(z_abs) * 0.20)))
    top20 = np.sort(z_abs)[-top20_n:]
    row = {
        "robust_z_median_abs": float(np.nanmedian(z_abs)),
        "robust_z_p90_abs": static_score,
        "l1_distance_to_baseline": float(
            np.nanmean(np.abs(med - baseline["median"]))
        ),
        "l2_distance_to_baseline": float(
            np.sqrt(np.nanmean((med - baseline["median"]) ** 2))
        ),
        "cosine_distance_to_baseline": cosine_distance(
            mean, baseline["median"]
        ),
        "top20_subcarrier_deviation_mean": float(np.nanmean(top20)),
        "static_score": static_score,
        "motion_score": float(motion_score),
    }
    row.update(motion)
    row["score_static"] = row["static_score"]
    row["score_motion"] = row["motion_score"]
    row["score_max"] = max(row["static_score"], row["motion_score"])
    row["score_weighted_s70_m30"] = (
        0.7 * row["static_score"] + 0.3 * row["motion_score"]
    )
    row["score_weighted_s50_m50"] = (
        0.5 * row["static_score"] + 0.5 * row["motion_score"]
    )
    row["score_weighted_s30_m70"] = (
        0.3 * row["static_score"] + 0.7 * row["motion_score"]
    )

    profile = np.nanmean(win, axis=0)
    b_mean = baseline["baseline_amp_mean"]
    w_shape = (profile - np.nanmean(profile)) / max(
        float(np.nanstd(profile)), 1e-6
    )
    b_shape = baseline["baseline_shape"]
    shape_corr = safe_corr(w_shape, b_shape)
    baseline_corr = safe_corr(profile, b_mean)
    row.update(
        {
            "baseline_mae": float(np.nanmean(np.abs(profile - b_mean))),
            "baseline_rmse": float(
                np.sqrt(np.nanmean((profile - b_mean) ** 2))
            ),
            "shape_rmse": float(
                np.sqrt(np.nanmean((w_shape - b_shape) ** 2))
            ),
            "shape_corr": shape_corr,
            "baseline_corr": baseline_corr,
            "shape_score": float(max(0.0, 1.0 - shape_corr)),
        }
    )
    row["final_score"] = robust_max_shape_score(row)
    row["robust_max_shape_score"] = row["final_score"]
    return row


def stage1_feature_vector(
    window_amplitude: np.ndarray,
    baseline_state: BaselineState,
) -> dict[str, float]:
    if len(window_amplitude) < baseline_state.window_size:
        raise ValueError(
            f"need at least {baseline_state.window_size} packets for "
            f"Stage-1 window, got {len(window_amplitude)}"
        )
    if len(window_amplitude) > baseline_state.window_size:
        window_amplitude = window_amplitude[-baseline_state.window_size :]
    win = preprocess_amplitude_segment(
        window_amplitude,
        segment=baseline_state.segment,
        preprocessing_type=baseline_state.preprocessing_type,
        row_start=0,
        row_end=baseline_state.window_size,
    )
    features = baseline_plus_shape_features(
        win, baseline_state.as_scoring_baseline()
    )
    return json_ready_floats(features)


def build_motion_input(
    window_amplitude: np.ndarray,
    segment: str = DEFAULT_SEGMENT,
    preprocessing_type: str = STAGE2_NORMALIZATION,
    window_size: int = WINDOW_SIZE,
) -> dict[str, Any]:
    if len(window_amplitude) < window_size:
        raise ValueError(
            f"need at least {window_size} packets for Stage-2 motion_input, "
            f"got {len(window_amplitude)}"
        )
    if len(window_amplitude) > window_size:
        window_amplitude = window_amplitude[-window_size:]
    matrix = preprocess_amplitude_segment(
        window_amplitude,
        segment=segment,
        preprocessing_type=preprocessing_type,
        row_start=0,
        row_end=window_size,
    )
    matrix = np.nan_to_num(
        matrix.astype(np.float32),
        nan=0.0,
        posinf=0.0,
        neginf=0.0,
    )
    if list(matrix.shape) != [128, 192]:
        raise ValueError(
            "motion_input values must be [128, 192], "
            f"got {list(matrix.shape)}"
        )
    payload = {
        "input_type": "window_matrix",
        "feature_version": STAGE2_FEATURE_VERSION,
        "shape": [128, 192],
        "values": matrix.tolist(),
    }
    validate_motion_input(payload)
    return payload


def json_ready_floats(features: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for key, value in features.items():
        if isinstance(value, (float, int, np.floating, np.integer)):
            number = float(value)
            out[key] = number if math.isfinite(number) else 0.0
    return out


def validate_motion_input(motion_input: dict[str, Any]) -> None:
    if motion_input.get("shape") != [128, 192]:
        raise ValueError(
            "motion_input.shape must be [128, 192], "
            f"got {motion_input.get('shape')}"
        )
    values = motion_input.get("values")
    if not isinstance(values, list):
        raise ValueError("motion_input.values must be a list")
    if len(values) != 128:
        raise ValueError(
            f"motion_input.values must have 128 rows, got {len(values)}"
        )
    for index, row in enumerate(values):
        if not isinstance(row, list):
            raise ValueError(
                f"motion_input.values[{index}] must be a list"
            )
        if len(row) != 192:
            raise ValueError(
                f"motion_input.values[{index}] must have 192 columns, "
                f"got {len(row)}"
            )
    matrix = np.asarray(values, dtype=np.float32)
    if not np.isfinite(matrix).all():
        raise ValueError(
            "motion_input.values contains NaN or infinite values"
        )
