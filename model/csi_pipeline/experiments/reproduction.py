"""Experiment entry points for room4_2026-05-27 reproduction runs."""

from __future__ import annotations

from pathlib import Path
import time

import numpy as np
import pandas as pd

from csi_pipeline.data import TARGET_ENV, TARGET_LABELS, default_dataset_root, load_env, load_train_envs
from csi_pipeline.stage1.calibration import build_stage1_test_table
from csi_pipeline.stage1.inference import apply_detector
from csi_pipeline.stage1.threshold import metric_dict
from csi_pipeline.stage1.train import fit_stage1_detector
from csi_pipeline.stage2.dataset import build_stage2_arrays
from csi_pipeline.stage2.evaluate import evaluate_static_moving
from csi_pipeline.stage2.inference import predict
from csi_pipeline.stage2.train import save_stage2_checkpoint, split_train_valid, train_cnn


def stage1_pass_meta(target_env: dict, detector: dict, preprocessing_type: str, segment: str = "full_buffer", window_size: int = 128, stride: int = 64) -> pd.DataFrame:
    test_df, _, _ = build_stage1_test_table(target_env, preprocessing_type, "guided 20%", segment, 20.0, 20.0, window_size, stride)
    pred = apply_detector(test_df, detector)
    return pred[["session_id", "start_frame", "end_frame", "pred_binary", "final_score"]].copy()


def run_end_to_end_room4_2026_05_27(
    output_root: str | Path = "outputs/csi_pipeline_refactor_v1",
    dataset_root: str | Path | None = None,
    stage1_preprocessing: str = "gain_mul_corrected",
    stage2_preprocessing: str = "packet_mean_normalized",
    segment: str = "full_buffer",
    window_size: int = 128,
    stride: int = 64,
    cnn_epochs: int = 25,
    seed: int = 42,
) -> pd.DataFrame:
    root = default_dataset_root() if dataset_root is None else Path(dataset_root)
    out_root = Path(output_root)
    out_root.mkdir(parents=True, exist_ok=True)

    train_envs = load_train_envs(root)
    target_env = load_env(root, TARGET_ENV, TARGET_LABELS)
    stage1_detector = fit_stage1_detector(
        train_envs,
        stage1_preprocessing,
        segment=segment,
        feature_set="baseline_plus_shape",
        detector="robust_max_shape",
        threshold_policy="balanced_macro",
        window_size=window_size,
        stride=stride,
        seed=seed,
    )
    stage1_test, stage1_cal_windows, stage1_notes = build_stage1_test_table(target_env, stage1_preprocessing, "guided 20%", segment, 20.0, 20.0, window_size, stride)
    stage1_pred = apply_detector(stage1_test, stage1_detector)
    stage1_metrics = metric_dict(stage1_pred["true_binary"].to_numpy(dtype=int), stage1_pred["pred_binary"].to_numpy(dtype=int))

    train_env_list = [train_envs[e] for e in train_envs]
    x_train, meta_train = build_stage2_arrays(train_env_list, stage2_preprocessing, segment, window_size, stride)
    x_test, meta_test = build_stage2_arrays([target_env], stage2_preprocessing, segment, window_size, stride, only_target_occupied=True)
    y_train = meta_train["class_id"].to_numpy(dtype=int)
    y_test = meta_test["class_id"].to_numpy(dtype=int)
    train_idx, valid_idx = split_train_valid(y_train, seed)
    batch_size = 256 if len(x_train) >= 256 else max(16, min(128, len(x_train)))
    started = time.perf_counter()
    stage2_result = train_cnn(x_train, y_train, train_idx, valid_idx, n_classes=2, epochs=cnn_epochs, batch_size=batch_size, seed=seed, device_name="auto", class_weight=True)
    train_time_sec = time.perf_counter() - started
    checkpoint_path = save_stage2_checkpoint(
        out_root / "stage2_small_cnn_packet_mean_normalized.pt",
        stage2_result,
        x_train,
        stage2_preprocessing,
        segment,
        window_size,
        stride,
        seed,
        {"train_time_sec": train_time_sec, "train_windows_total": int(len(x_train)), "num_train_windows": int(len(train_idx)), "num_valid_windows": int(len(valid_idx))},
    )

    oracle_pred = predict(stage2_result.model, x_test)
    oracle_metrics = evaluate_static_moving(y_test, oracle_pred)

    cascade_stage1_detector = stage1_detector
    cascade_stage1_preprocessing = stage1_preprocessing
    if stage2_preprocessing != stage1_preprocessing:
        cascade_stage1_preprocessing = stage2_preprocessing
        cascade_stage1_detector = fit_stage1_detector(
            train_envs,
            cascade_stage1_preprocessing,
            segment=segment,
            feature_set="baseline_plus_shape",
            detector="robust_max_shape",
            threshold_policy="balanced_macro",
            window_size=window_size,
            stride=stride,
            seed=seed,
        )
    pass_meta = stage1_pass_meta(target_env, cascade_stage1_detector, cascade_stage1_preprocessing, segment, window_size, stride)
    merged = meta_test.merge(pass_meta, left_on=["session_id", "window_start", "window_end"], right_on=["session_id", "start_frame", "end_frame"], how="left")
    mask = merged["pred_binary"].fillna(0).astype(int).to_numpy() == 1
    pass_rate = float(mask.mean()) if len(mask) else np.nan
    miss_count = int((~mask).sum())
    x_cascade = x_test[mask]
    y_cascade = y_test[mask]
    cascade_metrics = evaluate_static_moving(y_cascade, predict(stage2_result.model, x_cascade), pass_rate, miss_count) if len(y_cascade) else {}

    rows = [
        {
            "stage": "stage1",
            "mode": "guided20",
            "preprocessing_type": stage1_preprocessing,
            "model_type": "robust_max_shape",
            "macro_f1": stage1_metrics["macro_f1"],
            "accuracy": stage1_metrics["accuracy"],
            "empty_recall": stage1_metrics["empty_recall"],
            "occupied_recall": stage1_metrics["occupied_recall"],
            "num_calibration_windows": stage1_cal_windows,
            "checkpoint_path": "",
            "notes": stage1_notes,
        },
        {
            "stage": "stage2",
            "mode": "oracle_occupied_stage2",
            "preprocessing_type": stage2_preprocessing,
            "model_type": "small_cnn",
            "macro_f1": oracle_metrics["macro_f1"],
            "accuracy": oracle_metrics["accuracy"],
            "static_recall": oracle_metrics["static_recall"],
            "moving_recall": oracle_metrics["moving_recall"],
            "best_epoch": stage2_result.best_epoch,
            "checkpoint_path": str(checkpoint_path),
            "notes": "Stage-2 evaluated only on ground-truth occupied room4 windows.",
        },
        {
            "stage": "stage2",
            "mode": "cascaded_stage2",
            "preprocessing_type": stage2_preprocessing,
            "model_type": "small_cnn",
            "macro_f1": cascade_metrics.get("macro_f1", np.nan),
            "accuracy": cascade_metrics.get("accuracy", np.nan),
            "static_recall": cascade_metrics.get("static_recall", np.nan),
            "moving_recall": cascade_metrics.get("moving_recall", np.nan),
            "stage1_pass_rate": pass_rate,
            "stage1_miss_count": miss_count,
            "best_epoch": stage2_result.best_epoch,
            "checkpoint_path": str(checkpoint_path),
            "notes": f"Stage-2 evaluated only on windows passed by Stage-1 guided20 using {cascade_stage1_preprocessing}.",
        },
    ]
    result = pd.DataFrame(rows)
    result.to_csv(out_root / "end_to_end_reproduction_results.csv", index=False, encoding="utf-8-sig")
    return result
