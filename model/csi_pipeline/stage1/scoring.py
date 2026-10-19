"""Stage-1 feature and score calculations for the robust baseline detector."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from csi_pipeline.stage1.baseline import preprocess_session_segment, window_motion_features


BASELINE_ORIGINAL_FEATURES = [
    "robust_z_median_abs",
    "robust_z_p90_abs",
    "l1_distance_to_baseline",
    "l2_distance_to_baseline",
    "cosine_distance_to_baseline",
    "top20_subcarrier_deviation_mean",
    "static_score",
    "motion_score",
    "temporal_std_mean",
    "temporal_std_p95",
    "diff_abs_mean",
    "diff_abs_p95",
    "top10_diff_mean",
    "diff_energy_sum",
    "score_max",
    "score_weighted_s70_m30",
    "score_weighted_s50_m50",
    "score_weighted_s30_m70",
]

SHAPE_FEATURES = [
    "baseline_mae",
    "baseline_rmse",
    "shape_rmse",
    "shape_corr",
    "baseline_corr",
    "shape_score",
]

FEATURE_SETS = {
    "baseline_only_original": BASELINE_ORIGINAL_FEATURES,
    "baseline_plus_shape": BASELINE_ORIGINAL_FEATURES + SHAPE_FEATURES,
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


def baseline_plus_shape_features(win: np.ndarray, baseline: dict, clip: float = 20.0) -> dict[str, float]:
    med = np.nanmedian(win, axis=0)
    mean = np.nanmean(win, axis=0)
    z_abs = np.abs(med - baseline["median"]) / baseline["robust_scale"]
    z_abs = np.clip(z_abs, 0, clip)
    motion = window_motion_features(win)
    static_score = float(np.nanpercentile(z_abs, 90))
    motion_score = max(
        motion["diff_abs_mean"] / float(baseline["diff_abs_p95_scale"]),
        motion["temporal_std_mean"] / float(baseline["temporal_std_p95_scale"]),
    )
    top20_n = max(1, int(math.ceil(len(z_abs) * 0.20)))
    top20 = np.sort(z_abs)[-top20_n:]
    row = {
        "robust_z_median_abs": float(np.nanmedian(z_abs)),
        "robust_z_p90_abs": static_score,
        "l1_distance_to_baseline": float(np.nanmean(np.abs(med - baseline["median"]))),
        "l2_distance_to_baseline": float(np.sqrt(np.nanmean((med - baseline["median"]) ** 2))),
        "cosine_distance_to_baseline": cosine_distance(mean, baseline["median"]),
        "top20_subcarrier_deviation_mean": float(np.nanmean(top20)),
        "static_score": static_score,
        "motion_score": float(motion_score),
    }
    row.update(motion)
    row["score_static"] = row["static_score"]
    row["score_motion"] = row["motion_score"]
    row["score_max"] = max(row["static_score"], row["motion_score"])
    row["score_weighted_s70_m30"] = 0.7 * row["static_score"] + 0.3 * row["motion_score"]
    row["score_weighted_s50_m50"] = 0.5 * row["static_score"] + 0.5 * row["motion_score"]
    row["score_weighted_s30_m70"] = 0.3 * row["static_score"] + 0.7 * row["motion_score"]

    profile = np.nanmean(win, axis=0)
    b_mean = baseline["baseline_amp_mean"]
    w_shape = (profile - np.nanmean(profile)) / max(float(np.nanstd(profile)), 1e-6)
    b_shape = baseline["baseline_shape"]
    shape_corr = safe_corr(w_shape, b_shape)
    baseline_corr = safe_corr(profile, b_mean)
    row.update(
        {
            "baseline_mae": float(np.nanmean(np.abs(profile - b_mean))),
            "baseline_rmse": float(np.sqrt(np.nanmean((profile - b_mean) ** 2))),
            "shape_rmse": float(np.sqrt(np.nanmean((w_shape - b_shape) ** 2))),
            "shape_corr": shape_corr,
            "baseline_corr": baseline_corr,
            "shape_score": float(max(0.0, 1.0 - shape_corr)),
        }
    )
    row["final_score"] = robust_max_shape_score(row)
    return row


def robust_max_shape_score(features: pd.DataFrame | dict) -> np.ndarray | float:
    if isinstance(features, pd.DataFrame):
        return np.maximum.reduce([features["static_score"], features["motion_score"], features["shape_score"]])
    return float(max(features["static_score"], features["motion_score"], features["shape_score"]))


def feature_rows_for_range(
    session: dict,
    segment: str,
    baseline: dict,
    preprocessing_type: str,
    row_start: int,
    row_end: int,
    window_size: int,
    stride: int,
    source_session: str,
    update_used_before_prediction: bool,
) -> list[dict]:
    rows = []
    if row_end - row_start < window_size:
        return rows
    for start in range(row_start, row_end - window_size + 1, stride):
        win = preprocess_session_segment(session, segment, preprocessing_type, start, start + window_size)
        binary = 0 if session["label"] == "empty" else 1
        row = {
            "env_id": session["env_id"],
            "room_id": session["room_id"],
            "session_id": session["session_id"],
            "source_session": source_session,
            "original_label": session["label"],
            "true_binary": binary,
            "binary_true_label": "occupied" if binary else "empty",
            "start_frame": start,
            "end_frame": start + window_size,
            "time_sec": start * session["seconds_per_row"],
            "update_used_before_prediction": update_used_before_prediction,
        }
        row.update(baseline_plus_shape_features(win, baseline))
        rows.append(row)
    return rows
