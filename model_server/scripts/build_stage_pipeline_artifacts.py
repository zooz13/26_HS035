from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = REPO_ROOT.parent
CSI_PIPELINE_ROOT = WORKSPACE_ROOT / "csi-pipeline"
DATASET_ROOT = WORKSPACE_ROOT / "dataset"
MANIFEST_DIR = WORKSPACE_ROOT / "dataset_audit" / "outputs" / "model_training_manifest"
STAGE1_ARTIFACT_DIR = REPO_ROOT / "artifacts" / "models" / "stage1" / "stage1_threshold_v001"
LEGACY_ARTIFACT_DIR = REPO_ROOT / "artifacts" / "models" / "stage1" / "stage1_v001"
REPORT_PATH = REPO_ROOT / "docs" / "pipeline_design_verification_report.md"

if str(CSI_PIPELINE_ROOT) not in sys.path:
    sys.path.insert(0, str(CSI_PIPELINE_ROOT))

from csi_pipeline.data import (  # noqa: E402
    duration_to_rows,
    load_env,
    parse_env_id,
    parse_scenario,
    session_by_id,
)
from csi_pipeline.stage1.baseline import build_normalized_baseline, segment_amp  # noqa: E402
from csi_pipeline.stage1.calibration import ratio_rows  # noqa: E402
from csi_pipeline.stage1.scoring import FEATURE_SETS, feature_rows_for_range, robust_max_shape_score  # noqa: E402


PREPROCESSING_TYPE = "gain_mul_corrected"
SEGMENT = "full_buffer"
WINDOW_SIZE = 128
STRIDE = 64
CALIBRATION_SECONDS = 20.0
EMPTY_QUANTILE = 0.95
PRIMARY_EXCLUDED = {"room5_2026-05-08"}
CALIBRATED_HOLDOUTS = ["room4_2026-05-20", "room4_2026-05-27"]
GLOBAL_TRAIN = [
    "room1_2026-04-30",
    "room1_2026-05-07",
    "room2_2026-05-05",
    "room3_2026-05-05_rep01",
    "room3_2026-05-05_rep02",
    "room3_2026-05-07",
]
GLOBAL_VAL = ["room4_2026-05-07_rep01"]
GLOBAL_TEST = ["room4_2026-05-07_rep02"]
LABEL_MAP_STAGE1 = {"empty": "empty", "static_person": "occupied", "moving_person": "occupied"}
LABEL_MAP_STAGE2 = {"static_person": 0, "moving_person": 1}


def main() -> int:
    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    STAGE1_ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    sessions = collect_dataset_sessions()
    stage1_manifest = build_stage1_manifest(sessions)
    stage2_manifest = build_stage2_manifest(sessions)
    write_json(MANIFEST_DIR / "stage1_manifest.json", stage1_manifest)
    write_json(MANIFEST_DIR / "stage2_manifest.json", stage2_manifest)
    write_text(MANIFEST_DIR / "split_stage1_loro.yaml", stage1_split_yaml())
    write_text(MANIFEST_DIR / "split_stage2.yaml", stage2_split_yaml())
    write_text(MANIFEST_DIR / "dataset_selection_report.md", dataset_selection_report(sessions))

    detector, global_metrics = run_global_stage1_evaluation()
    calibrated_metrics = run_calibrated_holdout_evaluation(detector)
    write_stage1_artifact(detector, global_metrics, calibrated_metrics)
    mark_legacy_logreg_artifact()
    write_report(stage1_manifest, stage2_manifest, global_metrics, calibrated_metrics, detector)

    print(
        json.dumps(
            {
                "stage1_artifact_dir": str(STAGE1_ARTIFACT_DIR),
                "manifest_dir": str(MANIFEST_DIR),
                "report_path": str(REPORT_PATH),
                "global_test_macro_f1": global_metrics["test"]["macro_f1"],
                "calibrated_holdouts": {
                    item["target_env"]: {
                        "macro_f1": item["metrics"]["macro_f1"],
                        "empty_recall": item["metrics"]["empty_recall"],
                        "occupied_recall": item["metrics"]["occupied_recall"],
                    }
                    for item in calibrated_metrics["holdouts"]
                },
            },
            indent=2,
        )
    )
    return 0


def collect_dataset_sessions() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for env_path in sorted(path for path in DATASET_ROOT.iterdir() if path.is_dir()):
        env_id = env_path.name
        room_id, date_id, variable = parse_env_id(env_id)
        scenario_rows = parse_scenario(env_path / "scenario.md")
        for csv_path in sorted(env_path.glob("*.csv"), key=lambda p: p.name.lower()):
            session_id = csv_path.stem.upper()
            scenario = scenario_rows.get(session_id)
            scenario_label = scenario.label if scenario else "unknown"
            inferred_label = infer_label_from_session_id(session_id)
            needs_review = scenario_label not in {"unknown", inferred_label}
            rows.append(
                {
                    "env_id": env_id,
                    "room_id": room_id,
                    "date_id": date_id,
                    "variable": variable,
                    "session_id": session_id,
                    "csv_path": str(csv_path.relative_to(WORKSPACE_ROOT)),
                    "scenario_label": scenario_label,
                    "filename_inferred_label": inferred_label,
                    "needs_review": bool(needs_review),
                    "duration_sec": scenario.duration_sec if scenario else None,
                    "primary_excluded": env_id in PRIMARY_EXCLUDED,
                    "calibrated_holdout_candidate": env_id in CALIBRATED_HOLDOUTS,
                }
            )
    return rows


def infer_label_from_session_id(session_id: str) -> str:
    try:
        number = int(session_id.upper().lstrip("S"))
    except ValueError:
        return "unknown"
    pattern = {1: "empty", 2: "static_person", 3: "moving_person", 4: "empty", 5: "static_person", 6: "moving_person", 7: "empty"}
    return pattern.get(number, "unknown")


def build_stage1_manifest(sessions: list[dict[str, Any]]) -> dict[str, Any]:
    entries = []
    for row in sessions:
        label = row["scenario_label"]
        if label not in LABEL_MAP_STAGE1:
            continue
        entry = dict(row)
        entry["stage1_binary_label"] = LABEL_MAP_STAGE1[label]
        entry["used_for_primary_training"] = (
            not row["primary_excluded"] and not row["calibrated_holdout_candidate"] and row["env_id"] in GLOBAL_TRAIN
        )
        entry["used_for_global_validation"] = row["env_id"] in GLOBAL_VAL
        entry["used_for_global_test"] = row["env_id"] in GLOBAL_TEST
        entry["baseline_role"] = "empty_baseline_candidate" if label == "empty" else "not_baseline"
        entry["calibration_role"] = "target_empty_20_percent_candidate" if label == "empty" else "not_calibration"
        entries.append(entry)
    return {
        "manifest_version": "stage1_manifest_v001",
        "created_at": now_iso(),
        "source_dataset_root": str(DATASET_ROOT.relative_to(WORKSPACE_ROOT)),
        "random_window_split_used": False,
        "split_unit": "room/session group",
        "label_mapping": {"empty": 0, "occupied": 1, "source_mapping": LABEL_MAP_STAGE1},
        "normalization_version": "norm_v1_log_amp_empty_robust_zscore",
        "baseline_policy": {
            "source": "empty sessions only",
            "default_initial_session": "S01 when available",
            "center": "median",
            "scale": "MAD",
            "robust_scale": "1.4826 * MAD + eps",
            "clip": [-5, 5],
        },
        "calibration_policy": {
            "method": "empty_quantile",
            "empty_quantile": EMPTY_QUANTILE,
            "target_empty_fraction": 0.20,
            "calibration_windows_excluded_from_test": True,
        },
        "sessions": entries,
    }


def build_stage2_manifest(sessions: list[dict[str, Any]]) -> dict[str, Any]:
    entries = []
    for row in sessions:
        label = row["scenario_label"]
        if label not in LABEL_MAP_STAGE2:
            continue
        entry = dict(row)
        entry["stage2_class_id"] = LABEL_MAP_STAGE2[label]
        entry["used_for_primary_training"] = (
            not row["primary_excluded"] and not row["calibrated_holdout_candidate"] and row["env_id"] in GLOBAL_TRAIN
        )
        entry["used_for_global_validation"] = row["env_id"] in GLOBAL_VAL
        entry["used_for_global_test"] = row["env_id"] in GLOBAL_TEST
        entries.append(entry)
    return {
        "manifest_version": "stage2_manifest_v001",
        "created_at": now_iso(),
        "source_dataset_root": str(DATASET_ROOT.relative_to(WORKSPACE_ROOT)),
        "random_window_split_used": False,
        "split_unit": "room/session group",
        "stage2_scope": "Stage 2 consumes windows that Stage 1 has passed as occupied.",
        "excluded_labels": ["empty"],
        "label_mapping": LABEL_MAP_STAGE2,
        "sessions": entries,
    }


def run_global_stage1_evaluation() -> tuple[dict[str, Any], dict[str, Any]]:
    train_df = concat_stage1_tables(GLOBAL_TRAIN)
    val_df = concat_stage1_tables(GLOBAL_VAL)
    test_df = concat_stage1_tables(GLOBAL_TEST)
    detector = fit_detector(
        train_df,
        detector_name="robust_max_shape",
        feature_set="baseline_plus_shape",
        threshold_policy="balanced_macro",
    )
    metrics = {
        "evaluation_type": "global",
        "preprocessing_type": PREPROCESSING_TYPE,
        "segment": SEGMENT,
        "window_size": WINDOW_SIZE,
        "stride": STRIDE,
        "train_groups": GLOBAL_TRAIN,
        "validation_groups": GLOBAL_VAL,
        "test_groups": GLOBAL_TEST,
        "selected_threshold": detector["threshold"],
        "threshold_selection": detector["train_threshold_metrics"],
        "train": evaluate_table(train_df, detector),
        "validation": evaluate_table(val_df, detector),
        "test": evaluate_table(test_df, detector),
    }
    return detector, metrics


def concat_stage1_tables(env_ids: list[str]) -> pd.DataFrame:
    parts = []
    for env_id in env_ids:
        env = load_env(DATASET_ROOT, env_id)
        table = train_table_stage1_local(env, PREPROCESSING_TYPE, SEGMENT, CALIBRATION_SECONDS, WINDOW_SIZE, STRIDE)
        if not table.empty:
            parts.append(table)
    if not parts:
        raise RuntimeError(f"no Stage 1 windows generated for {env_ids}")
    return pd.concat(parts, ignore_index=True)


def evaluate_table(df: pd.DataFrame, detector: dict[str, Any]) -> dict[str, Any]:
    pred = apply_detector_local(df, detector)
    metrics = metric_dict_local(pred["true_binary"].to_numpy(dtype=int), pred["pred_binary"].to_numpy(dtype=int))
    metrics["num_windows"] = int(len(pred))
    metrics["static_person_recall"] = source_recall(pred, "static_person")
    metrics["moving_person_recall"] = source_recall(pred, "moving_person")
    metrics["score_summary"] = score_summary(pred["final_score"].to_numpy(dtype=float))
    metrics["confusion_matrix"] = [[metrics.pop("tn"), metrics.pop("fp")], [metrics.pop("fn"), metrics.pop("tp")]]
    return metrics


def train_table_stage1_local(
    env: dict[str, Any],
    preprocessing_type: str,
    segment: str,
    calibration_seconds: float,
    window_size: int,
    stride: int,
) -> pd.DataFrame:
    cal = env["calibration_session"]
    if cal is None:
        return pd.DataFrame()
    cal_rows, _, _ = duration_to_rows(calibration_seconds, cal["timestamp"], len(segment_amp(cal, segment)))
    baseline, _ = build_normalized_baseline([(cal, cal_rows)], segment, preprocessing_type, window_size, stride)
    out = []
    for session in env["sessions"]:
        if session["label"] not in LABEL_MAP_STAGE1:
            continue
        start_row = cal_rows if session["session_id"] == "S01" and session["label"] == "empty" else 0
        source = "S01_eval" if session["session_id"] == "S01" and session["label"] == "empty" else session["session_id"]
        out.extend(
            feature_rows_for_range(
                session,
                segment,
                baseline,
                preprocessing_type,
                start_row,
                len(segment_amp(session, segment)),
                window_size,
                stride,
                source,
                False,
            )
        )
    return pd.DataFrame(out)


def fit_detector(
    train_df: pd.DataFrame,
    detector_name: str,
    feature_set: str,
    threshold_policy: str,
) -> dict[str, Any]:
    if detector_name != "robust_max_shape":
        raise ValueError(f"unsupported detector: {detector_name}")
    if feature_set not in FEATURE_SETS:
        raise ValueError(f"unknown feature set: {feature_set}")
    if threshold_policy != "balanced_macro":
        raise ValueError(f"unsupported threshold policy: {threshold_policy}")
    train = train_df.copy()
    train["detector_score"] = robust_max_shape_score(train)
    threshold, threshold_metrics = choose_threshold_local(
        train["true_binary"].to_numpy(dtype=int),
        train["detector_score"].to_numpy(dtype=float),
    )
    return {
        "detector_name": detector_name,
        "feature_set": feature_set,
        "features": FEATURE_SETS[feature_set],
        "model": "robust_max_shape_rule",
        "threshold": threshold,
        "train_threshold_metrics": threshold_metrics,
    }


def choose_threshold_local(y_true: np.ndarray, scores: np.ndarray) -> tuple[float, dict[str, Any]]:
    finite = scores[np.isfinite(scores)]
    candidates = np.array([0.5]) if finite.size == 0 else np.unique(np.quantile(finite, np.linspace(0.005, 0.995, 220)))
    best: tuple[tuple[float, float, float], float, dict[str, Any]] | None = None
    for threshold in candidates:
        pred = (scores >= threshold).astype(int)
        metrics = metric_dict_local(y_true, pred)
        key = (-float(metrics["macro_f1"]), float(metrics["empty_false_alarm_rate"]), float(metrics["occupied_miss_rate"]))
        if best is None or key < best[0]:
            best = (key, float(threshold), metrics)
    if best is None:
        raise RuntimeError("could not select threshold")
    out = dict(best[2])
    out["threshold_policy"] = "balanced_macro"
    out["threshold_selection_reason"] = "selected_max_train_macro_f1"
    return best[1], out


def apply_detector_local(df: pd.DataFrame, detector_cfg: dict[str, Any]) -> pd.DataFrame:
    out = df.copy()
    score = robust_max_shape_score(out)
    threshold = float(detector_cfg["threshold"])
    out["final_score"] = score
    out["pred_binary"] = (score >= threshold).astype(int)
    out["pred_binary_label"] = np.where(out["pred_binary"].to_numpy(dtype=int) == 1, "occupied", "empty")
    return out


def metric_dict_local(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, Any]:
    y_true = np.asarray(y_true, dtype=int)
    y_pred = np.asarray(y_pred, dtype=int)
    tn = int(((y_true == 0) & (y_pred == 0)).sum())
    fp = int(((y_true == 0) & (y_pred == 1)).sum())
    fn = int(((y_true == 1) & (y_pred == 0)).sum())
    tp = int(((y_true == 1) & (y_pred == 1)).sum())
    empty_precision, empty_recall, empty_f1 = precision_recall_f1(tn, fn, fp)
    occupied_precision, occupied_recall, occupied_f1 = precision_recall_f1(tp, fp, fn)
    accuracy = float((tp + tn) / max(1, len(y_true)))
    return {
        "accuracy": accuracy,
        "macro_f1": float((empty_f1 + occupied_f1) / 2.0),
        "balanced_accuracy": float((empty_recall + occupied_recall) / 2.0),
        "empty_precision": empty_precision,
        "empty_recall": empty_recall,
        "empty_f1": empty_f1,
        "occupied_precision": occupied_precision,
        "occupied_recall": occupied_recall,
        "occupied_f1": occupied_f1,
        "occupied_miss_rate": float(1.0 - occupied_recall),
        "empty_false_alarm_rate": float(1.0 - empty_recall),
        "tn": tn,
        "fp": fp,
        "fn": fn,
        "tp": tp,
    }


def precision_recall_f1(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    precision = tp / max(1, tp + fp)
    recall = tp / max(1, tp + fn)
    f1 = 0.0 if precision + recall == 0.0 else 2.0 * precision * recall / (precision + recall)
    return float(precision), float(recall), float(f1)


def run_calibrated_holdout_evaluation(detector: dict[str, Any]) -> dict[str, Any]:
    holdouts = [evaluate_target_calibrated(env_id, detector) for env_id in CALIBRATED_HOLDOUTS]
    return {
        "evaluation_type": "target_room_calibrated",
        "preprocessing_type": PREPROCESSING_TYPE,
        "segment": SEGMENT,
        "window_size": WINDOW_SIZE,
        "stride": STRIDE,
        "calibration_policy": {
            "method": "empty_quantile",
            "empty_quantile": EMPTY_QUANTILE,
            "calibration_windows_excluded_from_test": True,
        },
        "base_global_threshold": detector["threshold"],
        "holdouts": holdouts,
    }


def evaluate_target_calibrated(env_id: str, detector: dict[str, Any]) -> dict[str, Any]:
    env = load_env(DATASET_ROOT, env_id)
    initial_empty = session_by_id(env, "S01")
    baseline_rows, _, seconds_per_row = duration_to_rows(CALIBRATION_SECONDS, initial_empty["timestamp"], len(segment_amp(initial_empty, SEGMENT)))
    baseline, baseline_windows = build_normalized_baseline(
        [(initial_empty, baseline_rows)],
        SEGMENT,
        PREPROCESSING_TYPE,
        WINDOW_SIZE,
        STRIDE,
    )
    calibration_session = choose_calibration_empty_session(env)
    cal_rows = ratio_rows(calibration_session, SEGMENT, 0.20, WINDOW_SIZE)
    calibration_df = pd.DataFrame(
        feature_rows_for_range(
            calibration_session,
            SEGMENT,
            baseline,
            PREPROCESSING_TYPE,
            0,
            cal_rows,
            WINDOW_SIZE,
            STRIDE,
            f"{calibration_session['session_id']}_empty_calibration_20pct",
            True,
        )
    )
    if calibration_df.empty:
        raise RuntimeError(f"no calibration windows generated for {env_id}")
    calibration_scores = robust_max_shape_score(calibration_df)
    selected_threshold = float(np.quantile(calibration_scores, EMPTY_QUANTILE))
    eval_rows: list[dict[str, Any]] = []
    for session in env["sessions"]:
        label = session["label"]
        if label not in LABEL_MAP_STAGE1:
            continue
        if session["session_id"] == "S01":
            continue
        start_row = cal_rows if session["session_id"] == calibration_session["session_id"] else 0
        if len(segment_amp(session, SEGMENT)) - start_row < WINDOW_SIZE:
            continue
        eval_rows.extend(
            feature_rows_for_range(
                session,
                SEGMENT,
                baseline,
                PREPROCESSING_TYPE,
                start_row,
                len(segment_amp(session, SEGMENT)),
                WINDOW_SIZE,
                STRIDE,
                f"{session['session_id']}_eval",
                session["session_id"] == calibration_session["session_id"],
            )
        )
    eval_df = pd.DataFrame(eval_rows)
    if eval_df.empty:
        raise RuntimeError(f"no evaluation windows generated for {env_id}")
    eval_scores = robust_max_shape_score(eval_df)
    pred_binary = (eval_scores >= selected_threshold).astype(int)
    metrics = metric_dict_local(eval_df["true_binary"].to_numpy(dtype=int), pred_binary)
    pred_df = eval_df.copy()
    pred_df["final_score"] = eval_scores
    pred_df["pred_binary"] = pred_binary
    metrics["num_windows"] = int(len(pred_df))
    metrics["static_person_recall"] = source_recall(pred_df, "static_person")
    metrics["moving_person_recall"] = source_recall(pred_df, "moving_person")
    metrics["confusion_matrix"] = [[metrics.pop("tn"), metrics.pop("fp")], [metrics.pop("fn"), metrics.pop("tp")]]
    return {
        "target_env": env_id,
        "baseline_session": "S01",
        "baseline_rows": int(baseline_rows),
        "baseline_seconds_estimate": float(baseline_rows * seconds_per_row),
        "baseline_windows": int(baseline_windows),
        "calibration_session": calibration_session["session_id"],
        "calibration_rows": int(cal_rows),
        "calibration_windows": int(len(calibration_df)),
        "calibration_windows_excluded_from_test": True,
        "global_threshold_reference": float(detector["threshold"]),
        "selected_threshold": selected_threshold,
        "empty_calibration_score_summary": quantile_summary(np.asarray(calibration_scores, dtype=float)),
        "score_distribution_summary": score_distribution_summary(pred_df),
        "metrics": metrics,
        "comparison_protocols": {
            "guided_baseline_global_threshold": evaluate_guided_baseline_global_threshold(env, detector, calibration_session, cal_rows)
        },
    }


def choose_calibration_empty_session(env: dict[str, Any]) -> dict[str, Any]:
    for session in env["sessions"]:
        if session["label"] == "empty" and session["session_id"] == "S04":
            return session
    for session in env["sessions"]:
        if session["label"] == "empty" and session["session_id"] != "S01":
            return session
    return session_by_id(env, "S01")


def evaluate_guided_baseline_global_threshold(
    env: dict[str, Any],
    detector: dict[str, Any],
    calibration_session: dict[str, Any],
    cal_rows: int,
) -> dict[str, Any]:
    initial_empty = session_by_id(env, "S01")
    baseline_rows, _, _ = duration_to_rows(CALIBRATION_SECONDS, initial_empty["timestamp"], len(segment_amp(initial_empty, SEGMENT)))
    baseline, baseline_windows = build_normalized_baseline(
        [(initial_empty, baseline_rows), (calibration_session, cal_rows)],
        SEGMENT,
        PREPROCESSING_TYPE,
        WINDOW_SIZE,
        STRIDE,
    )
    eval_rows: list[dict[str, Any]] = []
    for session in env["sessions"]:
        if session["label"] not in LABEL_MAP_STAGE1 or session["session_id"] == "S01":
            continue
        start_row = cal_rows if session["session_id"] == calibration_session["session_id"] else 0
        if len(segment_amp(session, SEGMENT)) - start_row < WINDOW_SIZE:
            continue
        eval_rows.extend(
            feature_rows_for_range(
                session,
                SEGMENT,
                baseline,
                PREPROCESSING_TYPE,
                start_row,
                len(segment_amp(session, SEGMENT)),
                WINDOW_SIZE,
                STRIDE,
                f"{session['session_id']}_eval_guided_global_threshold",
                session["session_id"] == calibration_session["session_id"],
            )
        )
    eval_df = pd.DataFrame(eval_rows)
    pred_df = apply_detector_local(eval_df, detector)
    metrics = metric_dict_local(pred_df["true_binary"].to_numpy(dtype=int), pred_df["pred_binary"].to_numpy(dtype=int))
    metrics["num_windows"] = int(len(pred_df))
    metrics["static_person_recall"] = source_recall(pred_df, "static_person")
    metrics["moving_person_recall"] = source_recall(pred_df, "moving_person")
    metrics["confusion_matrix"] = [[metrics.pop("tn"), metrics.pop("fp")], [metrics.pop("fn"), metrics.pop("tp")]]
    return {
        "description": "Code-path comparison matching csi_pipeline guided recalibration style: merge S01 baseline with S04 empty prefix, keep global threshold, and exclude S04 calibration windows.",
        "selected_threshold": float(detector["threshold"]),
        "baseline_sessions": ["S01", calibration_session["session_id"]],
        "baseline_windows": int(baseline_windows),
        "calibration_windows_excluded_from_test": True,
        "metrics": metrics,
        "score_distribution_summary": score_distribution_summary(pred_df),
    }


def source_recall(df: pd.DataFrame, source_label: str) -> float | None:
    subset = df[df["original_label"] == source_label]
    if subset.empty:
        return None
    return float((subset["pred_binary"].to_numpy(dtype=int) == 1).mean())


def quantile_summary(values: np.ndarray) -> dict[str, float]:
    values = values[np.isfinite(values)]
    if values.size == 0:
        return {"p50": math.nan, "p90": math.nan, "p95": math.nan, "p99": math.nan}
    return {
        "p50": float(np.quantile(values, 0.50)),
        "p90": float(np.quantile(values, 0.90)),
        "p95": float(np.quantile(values, 0.95)),
        "p99": float(np.quantile(values, 0.99)),
    }


def score_summary(values: np.ndarray) -> dict[str, float]:
    values = values[np.isfinite(values)]
    if values.size == 0:
        return {"min": math.nan, "p50": math.nan, "p95": math.nan, "max": math.nan}
    return {
        "min": float(np.min(values)),
        "p50": float(np.quantile(values, 0.50)),
        "p95": float(np.quantile(values, 0.95)),
        "max": float(np.max(values)),
    }


def score_distribution_summary(df: pd.DataFrame) -> dict[str, dict[str, float]]:
    out = {}
    for label, subset in df.groupby("original_label"):
        out[str(label)] = score_summary(subset["final_score"].to_numpy(dtype=float))
    return out


def write_stage1_artifact(detector: dict[str, Any], global_metrics: dict[str, Any], calibrated_metrics: dict[str, Any]) -> None:
    write_json(
        STAGE1_ARTIFACT_DIR / "stage1_threshold_config.json",
        {
            "model_id": "stage1_threshold_v001",
            "stage1_mode": "threshold",
            "detector_name": "robust_max_shape",
            "csi_pipeline_score_name": "robust_max_shape_score",
            "score_name": "server_window_score",
            "feature_set": detector["feature_set"],
            "features": detector["features"],
            "threshold_policy": "balanced_macro_global_reference_with_empty_quantile_calibration",
            "selected_threshold": float(detector["threshold"]),
            "server_score_note": "Server score is computed from an already baseline-normalized window matrix. Offline evaluation uses csi_pipeline robust_max_shape_score with a target empty baseline.",
        },
    )
    write_text(
        STAGE1_ARTIFACT_DIR / "feature_config.yaml",
        "\n".join(
            [
                "raw_csi_length: 384",
                "representation: real_imag_interleaved",
                "amplitude: sqrt_iq",
                "log_transform: log1p",
                "normalization_version: norm_v1_log_amp_empty_robust_zscore",
                "baseline_source: empty_sessions_only",
                "center: median",
                "scale: MAD",
                "robust_scale: 1.4826 * MAD + eps",
                "clip_range: [-5, 5]",
                f"preprocessing_type: {PREPROCESSING_TYPE}",
                f"segment: {SEGMENT}",
                f"window_size: {WINDOW_SIZE}",
                f"stride: {STRIDE}",
                "output_shape: [128, 192]",
                "",
            ]
        ),
    )
    write_json(STAGE1_ARTIFACT_DIR / "label_mapping.json", {"empty": 0, "occupied": 1, "source_mapping": LABEL_MAP_STAGE1})
    write_json(STAGE1_ARTIFACT_DIR / "metrics_global.json", global_metrics)
    write_json(STAGE1_ARTIFACT_DIR / "metrics_loro_calibrated.json", calibrated_metrics)
    write_json(
        STAGE1_ARTIFACT_DIR / "calibration_policy.json",
        {
            "calibration_id": "calib_default_v001",
            "stage1_artifact_id": "stage1_threshold_v001",
            "baseline_id": "provided_by_preprocessor",
            "method": "empty_quantile",
            "empty_quantile": EMPTY_QUANTILE,
            "margin": 0.0,
            "threshold": float(detector["threshold"]),
            "num_windows": 0,
            "score_summary": {"p50": 0.0, "p90": 0.0, "p95": 0.0, "p99": 0.0},
            "api_draft": [
                "POST /api/calibration/start",
                "POST /api/calibration/window",
                "POST /api/calibration/finish",
                "GET /api/calibration/status",
                "POST /api/calibration/reset",
            ],
        },
    )
    write_json(
        STAGE1_ARTIFACT_DIR / "metadata.json",
        {
            "model_id": "stage1_threshold_v001",
            "stage1_mode": "threshold",
            "task": "empty_occupied_gate",
            "labels": ["empty", "occupied"],
            "input_type": "window_matrix",
            "input_shape": [WINDOW_SIZE, 192],
            "checkpoint_file": None,
            "requires_model_pkl": False,
            "preprocess_version": "preprocess_v1",
            "normalization_version": "norm_v1_log_amp_empty_robust_zscore",
            "feature_version": "stage1_window_features_v1",
            "created_at": now_iso(),
            "source_generation_code": "model_server/scripts/build_stage_pipeline_artifacts.py",
            "source_pipeline_code": [
                "csi_pipeline/stage1/baseline.py",
                "csi_pipeline/stage1/scoring.py",
                "csi_pipeline/stage1/threshold.py",
                "csi_pipeline/stage1/calibration.py",
            ],
            "primary_excluded_groups": sorted(PRIMARY_EXCLUDED),
            "calibrated_holdout_groups": CALIBRATED_HOLDOUTS,
        },
    )


def mark_legacy_logreg_artifact() -> None:
    metadata_path = LEGACY_ARTIFACT_DIR / "metadata.json"
    if metadata_path.exists():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["legacy_status"] = "legacy_logreg_server_integration_proof_of_concept"
        metadata["not_final_stage1_pipeline"] = True
        metadata["legacy_reason"] = (
            "This artifact loads model.pkl and learned logistic-regression weights. "
            "The verified project Stage 1 design is a baseline-relative threshold detector."
        )
        write_json(metadata_path, metadata)
    write_text(
        LEGACY_ARTIFACT_DIR / "README.md",
        "stage1_v001 is a server integration test artifact, not final Stage 1 pipeline.\n"
        "Keep it for legacy comparison. Do not delete it when using stage1_threshold_v001.\n",
    )


def write_report(
    stage1_manifest: dict[str, Any],
    stage2_manifest: dict[str, Any],
    global_metrics: dict[str, Any],
    calibrated_metrics: dict[str, Any],
    detector: dict[str, Any],
) -> None:
    holdout_lines = []
    comparison_lines = []
    for holdout in calibrated_metrics["holdouts"]:
        metrics = holdout["metrics"]
        holdout_lines.append(
            f"| {holdout['target_env']} | {holdout['selected_threshold']:.6f} | "
            f"{metrics['macro_f1']:.6f} | {metrics['empty_recall']:.6f} | "
            f"{metrics['occupied_recall']:.6f} | {metrics['static_person_recall']:.6f} | "
            f"{metrics['moving_person_recall']:.6f} |"
        )
        comparison = holdout["comparison_protocols"]["guided_baseline_global_threshold"]
        comparison_metrics = comparison["metrics"]
        comparison_lines.append(
            f"| {holdout['target_env']} | {comparison['selected_threshold']:.6f} | "
            f"{comparison_metrics['macro_f1']:.6f} | {comparison_metrics['empty_recall']:.6f} | "
            f"{comparison_metrics['occupied_recall']:.6f} | {comparison_metrics['static_person_recall']:.6f} | "
            f"{comparison_metrics['moving_person_recall']:.6f} |"
        )
    test = global_metrics["test"]
    report = f"""# Pipeline Design Verification Report

Generated: {now_iso()}

## Evidence Policy

This report is based on Python source, configs, training/evaluation scripts, and generated artifacts. README files were treated as secondary notes only. When README text conflicts with code, the code path is treated as authoritative.

## Stage 1 Definition From Code

- Source files: `csi-pipeline/csi_pipeline/stage1/baseline.py`, `scoring.py`, `threshold.py`, `calibration.py`, `train.py`, `evaluate.py`.
- Stage 1 is a baseline-relative threshold detector, not a learned logistic regression or CNN.
- CSI parsing expects raw length 384 as interleaved I/Q and converts it to 192 amplitudes.
- The active refactored detector computes baseline-relative features and `robust_max_shape_score = max(static_score, motion_score, shape_score)`.
- `threshold.py::choose_threshold` selects a threshold over score candidates using training labels and the `balanced_macro` policy.
- `calibration.py::build_stage1_test_table` contains S01 initial empty baseline and guided S04 empty recalibration policies, and excludes the S04 recalibration prefix from evaluation.

## Stage 1 Score, Threshold, Baseline, Calibration Checks

1. Score calculation: `csi_pipeline.stage1.scoring.baseline_plus_shape_features` computes static, motion, and shape features; `robust_max_shape_score` is the final score.
2. Threshold decision: `csi_pipeline.stage1.threshold.choose_threshold` selects the threshold from training scores by maximizing macro F1 under `balanced_macro`.
3. Baseline sessions: `train_table_stage1` uses each env calibration session, preferring S01 empty, with the first 20 seconds by default.
4. Calibration sessions/ratio: `calibration.py` supports `guided 10%`, `guided 20%`, `guided 30%`, using an S04 empty prefix for guided recalibration.
5. Calibration exclusion: guided methods evaluate S04 from `s04_rows` onward, so calibration windows are excluded from metrics.

## Stage 2 Definition From Code

- Source files: `csi-pipeline/csi_pipeline/stage2/dataset.py`, `model.py`, `train.py`.
- Stage 2 is occupied-only static/moving classification.
- `dataset.py::build_stage2_arrays` filters labels to `static_person` and `moving_person`; empty is excluded.
- Label mapping is `static_person: 0`, `moving_person: 1`.
- `TinyCnn` exports a 2-class CNN checkpoint (`n_classes=2`).

## Current model_server Difference

- Previous config loaded `stage1_v001` with `stage1_mode: checkpoint`.
- `model_server/src/model_adapter/stage1_adapter.py` previously required `model.pkl` and applied learned logistic-regression weights.
- That does not match the verified Stage 1 threshold detector design.
- The server has been updated to support `stage1_mode: threshold` with `stage1_threshold_config.json` and no required `model.pkl`.
- Stage 2 server mode is set to `disabled`; it returns skipped for Stage 1 empty windows and unknown for occupied windows until a static/moving CNN is connected.

## README Conflicts

- `model_server/README.md` describes exporting and loading `stage1_v001/model.pkl` with `stage1_mode: checkpoint`.
- The verified `csi_pipeline` Stage 1 code defines a threshold detector. Therefore the README is stale for final pipeline design and only describes the legacy server integration artifact.

## stage1_v001 Decision

`stage1_v001` should not continue as the final Stage 1 artifact. It is marked legacy because it is a logistic-regression server integration proof-of-concept. It is preserved at `model_server/artifacts/models/stage1/stage1_v001`.

## New Artifacts

- Stage 1 threshold artifact: `model_server/artifacts/models/stage1/stage1_threshold_v001`
- Stage 1 manifest entries: {len(stage1_manifest['sessions'])}
- Stage 2 manifest entries: {len(stage2_manifest['sessions'])}

## Global Stage 1 Metrics

| split | threshold | macro_f1 | empty_recall | occupied_recall | static_recall | moving_recall |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| train | {detector['threshold']:.6f} | {global_metrics['train']['macro_f1']:.6f} | {global_metrics['train']['empty_recall']:.6f} | {global_metrics['train']['occupied_recall']:.6f} | {global_metrics['train']['static_person_recall']:.6f} | {global_metrics['train']['moving_person_recall']:.6f} |
| validation | {detector['threshold']:.6f} | {global_metrics['validation']['macro_f1']:.6f} | {global_metrics['validation']['empty_recall']:.6f} | {global_metrics['validation']['occupied_recall']:.6f} | {global_metrics['validation']['static_person_recall']:.6f} | {global_metrics['validation']['moving_person_recall']:.6f} |
| test | {detector['threshold']:.6f} | {test['macro_f1']:.6f} | {test['empty_recall']:.6f} | {test['occupied_recall']:.6f} | {test['static_person_recall']:.6f} | {test['moving_person_recall']:.6f} |

## Target-Room Calibrated Stage 1 Metrics

Protocol: S01 empty baseline, S04 first 20% empty score quantile threshold, calibration windows excluded from test.

| target_env | selected_threshold | macro_f1 | empty_recall | occupied_recall | static_recall | moving_recall |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
{chr(10).join(holdout_lines)}

## Code-Path Guided Baseline Comparison

Protocol: S01 baseline plus S04 first 20% guided baseline merge, global threshold retained, calibration windows excluded from test. This matches the direction implemented in `csi_pipeline.stage1.calibration.build_stage1_test_table` more closely than pure empty-quantile thresholding.

| target_env | selected_threshold | macro_f1 | empty_recall | occupied_recall | static_recall | moving_recall |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
{chr(10).join(comparison_lines)}

## Required Work To Return To Original Design

- Keep `stage1_v001` only as legacy.
- Use `stage1_threshold_v001` as the Stage 1 artifact.
- Keep baseline creation outside `model_server`; the server must receive preprocessed windows or calibration-profile inputs.
- Connect a true Stage 2 static/moving CNN before enabling `stage2_mode: static_moving_cnn`.
- Do not wire risk-level categories into Stage 2. Risk scoring remains a later pipeline stage.

## Files Not To Delete

- `model_server/artifacts/models/stage1/stage1_v001/model.pkl`
- `model_server/artifacts/models/stage1/stage1_v001/*.json`
- `dataset/**`

## Legacy Files

- `model_server/artifacts/models/stage1/stage1_v001`
- `model_server/scripts/train_stage1_model.py`

## Newly Required Artifact Files

- `stage1_threshold_config.json`
- `feature_config.yaml`
- `label_mapping.json`
- `metrics_global.json`
- `metrics_loro_calibrated.json`
- `calibration_policy.json`
- `metadata.json`
"""
    write_text(REPORT_PATH, report)


def dataset_selection_report(sessions: list[dict[str, Any]]) -> str:
    total = len(sessions)
    needs_review = sum(1 for row in sessions if row["needs_review"])
    excluded = sorted({row["env_id"] for row in sessions if row["primary_excluded"]})
    holdouts = sorted({row["env_id"] for row in sessions if row["calibrated_holdout_candidate"]})
    return f"""# Dataset Selection Report

Generated: {now_iso()}

- Total CSV sessions scanned: {total}
- Scenario/file-label mismatches requiring review: {needs_review}
- Excluded from first-pass training/evaluation: {', '.join(excluded)}
- Calibrated holdout candidates: {', '.join(holdouts)}
- Random window split used: false
- Split unit: room/session group

Scenario labels are read from `scenario.md` when present. Filename/session-id inferred labels are recorded only for review comparison.
"""


def stage1_split_yaml() -> str:
    return f"""split_name: split_stage1_loro_v001
random_window_split_used: false
split_unit: room_session_group
primary_excluded:
  - room5_2026-05-08
global_train:
{yaml_list(GLOBAL_TRAIN)}
global_validation:
{yaml_list(GLOBAL_VAL)}
global_test:
{yaml_list(GLOBAL_TEST)}
calibrated_holdout_candidates:
{yaml_list(CALIBRATED_HOLDOUTS)}
loro_policy:
  target_empty_baseline: S01 first 20 seconds when available
  calibration: S04 empty first 20 percent when available
  calibration_windows_excluded_from_test: true
"""


def stage2_split_yaml() -> str:
    return f"""split_name: split_stage2_static_moving_v001
random_window_split_used: false
split_unit: room_session_group
excluded_labels:
  - empty
label_mapping:
  static_person: 0
  moving_person: 1
primary_excluded:
  - room5_2026-05-08
train_groups:
{yaml_list(GLOBAL_TRAIN)}
validation_groups:
{yaml_list(GLOBAL_VAL)}
test_groups:
{yaml_list(GLOBAL_TEST)}
calibrated_holdout_candidates:
{yaml_list(CALIBRATED_HOLDOUTS)}
"""


def yaml_list(items: list[str]) -> str:
    return "\n".join(f"  - {item}" for item in items)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(to_jsonable(payload), indent=2, ensure_ascii=False), encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def to_jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [to_jsonable(item) for item in value]
    if isinstance(value, np.ndarray):
        return to_jsonable(value.tolist())
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


if __name__ == "__main__":
    raise SystemExit(main())
