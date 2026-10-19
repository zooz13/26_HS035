"""Stage-1 training-table creation and detector fitting."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from csi_pipeline.data import TRAIN_ENVS, default_dataset_root, load_env, load_train_envs
from csi_pipeline.stage1.baseline import build_normalized_baseline
from csi_pipeline.stage1.calibration import calibration_rows_seconds
from csi_pipeline.stage1.scoring import FEATURE_SETS, feature_rows_for_range, robust_max_shape_score
from csi_pipeline.stage1.threshold import choose_threshold


def fit_detector(train_df: pd.DataFrame, detector_name: str = "robust_max_shape", feature_set: str = "baseline_plus_shape", threshold_policy: str = "balanced_macro", seed: int = 42) -> dict:
    del seed
    if detector_name != "robust_max_shape":
        raise ValueError(f"unsupported detector: {detector_name}")
    if feature_set not in FEATURE_SETS:
        raise ValueError(f"unknown feature set: {feature_set}")
    train = train_df.copy()
    train["detector_score"] = robust_max_shape_score(train)
    threshold, threshold_metrics = choose_threshold(train, "detector_score", threshold_policy)
    return {
        "detector_name": detector_name,
        "feature_set": feature_set,
        "features": FEATURE_SETS[feature_set],
        "model": "robust_max_shape_rule",
        "threshold": threshold,
        "train_threshold_metrics": threshold_metrics,
    }


def train_table_stage1(env: dict, preprocessing_type: str, segment: str = "full_buffer", s01_calibration_seconds: float = 20.0, window_size: int = 128, stride: int = 64) -> pd.DataFrame:
    cal = env["calibration_session"]
    if cal is None:
        return pd.DataFrame()
    cal_rows = calibration_rows_seconds(cal, segment, s01_calibration_seconds, window_size)
    baseline, _ = build_normalized_baseline([(cal, cal_rows)], segment, preprocessing_type, window_size, stride)
    out = []
    for session in env["sessions"]:
        if session["label"] not in {"empty", "static_person", "moving_person"}:
            continue
        start_row = cal_rows if session["session_id"] == "S01" and session["label"] == "empty" else 0
        source = "S01_eval" if session["session_id"] == "S01" and session["label"] == "empty" else session["session_id"]
        out.extend(feature_rows_for_range(session, segment, baseline, preprocessing_type, start_row, len(session["amplitude"]), window_size, stride, source, False))
    return pd.DataFrame(out)


def fit_stage1_detector(
    train_envs: dict[str, dict],
    preprocessing_type: str,
    segment: str = "full_buffer",
    feature_set: str = "baseline_plus_shape",
    detector: str = "robust_max_shape",
    threshold_policy: str = "balanced_macro",
    s01_calibration_seconds: float = 20.0,
    window_size: int = 128,
    stride: int = 64,
    seed: int = 42,
) -> dict:
    parts = []
    for env_id in TRAIN_ENVS:
        table = train_table_stage1(train_envs[env_id], preprocessing_type, segment, s01_calibration_seconds, window_size, stride)
        if not table.empty:
            parts.append(table)
    train_df = pd.concat(parts, ignore_index=True)
    return fit_detector(train_df, detector, feature_set, threshold_policy, seed)


def fit_room4_excluded_stage1(
    dataset_root: str | Path | None = None,
    preprocessing_type: str = "gain_mul_corrected",
    segment: str = "full_buffer",
    feature_set: str = "baseline_plus_shape",
    detector: str = "robust_max_shape",
    threshold_policy: str = "balanced_macro",
    window_size: int = 128,
    stride: int = 64,
    seed: int = 42,
) -> dict:
    root = default_dataset_root() if dataset_root is None else Path(dataset_root)
    train_envs = load_train_envs(root)
    return fit_stage1_detector(train_envs, preprocessing_type, segment, feature_set, detector, threshold_policy, 20.0, window_size, stride, seed)


def build_stage1_training_table(
    dataset_root: str | Path | None = None,
    preprocessing_type: str = "gain_mul_corrected",
    segment: str = "full_buffer",
    window_size: int = 128,
    stride: int = 64,
) -> pd.DataFrame:
    root = default_dataset_root() if dataset_root is None else Path(dataset_root)
    parts = []
    for env_id in TRAIN_ENVS:
        table = train_table_stage1(load_env(root, env_id), preprocessing_type, segment, 20.0, window_size, stride)
        if not table.empty:
            parts.append(table)
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
