from __future__ import annotations

import argparse
import json
import math
import pickle
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = REPO_ROOT.parent
CSI_PIPELINE_ROOT = WORKSPACE_ROOT / "csi-pipeline"
if str(CSI_PIPELINE_ROOT) not in sys.path:
    sys.path.insert(0, str(CSI_PIPELINE_ROOT))

from csi_pipeline.preprocessing.csi_parser import load_amplitude_csv


MODEL_ID = "stage1_v001"
PREPROCESS_VERSION = "preprocess_v1"
NORMALIZATION_VERSION = "norm_v1_log_amp_empty_robust_zscore"
FEATURE_VERSION = "stage1_window_features_v1"

TRAIN_SPLIT = [
    "room1_2026-04-30",
    "room1_2026-05-07",
    "room2_2026-05-05",
    "room3_2026-05-05_rep01",
    "room3_2026-05-05_rep02",
    "room3_2026-05-07",
]
VAL_SPLIT = ["room4_2026-05-07_rep01"]
TEST_SPLIT = ["room4_2026-05-07_rep02"]
EXCLUDED_GROUPS = ["room4_2026-05-20", "room4_2026-05-27", "room5_2026-05-08"]
SOURCE_MAPPING = {"empty": "empty", "static_person": "occupied", "moving_person": "occupied"}
FEATURE_NAMES = [
    "mean_abs",
    "max_abs",
    "std",
    "rms",
    "p95_abs",
    "frac_abs_gt_1",
    "frac_abs_gt_2",
    "temporal_std_mean",
    "carrier_std_mean",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Train and export Stage 1 occupancy checkpoint artifacts.")
    parser.add_argument("--dataset-root", default=str(WORKSPACE_ROOT / "dataset"))
    parser.add_argument("--artifact-dir", default=str(REPO_ROOT / "artifacts" / "models" / "stage1" / MODEL_ID))
    parser.add_argument("--window-size", type=int, default=128)
    parser.add_argument("--stride", type=int, default=64)
    parser.add_argument("--epochs", type=int, default=1200)
    parser.add_argument("--learning-rate", type=float, default=0.08)
    parser.add_argument("--l2", type=float, default=0.001)
    args = parser.parse_args()

    dataset_root = Path(args.dataset_root)
    artifact_dir = Path(args.artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)

    train = build_split_features(dataset_root, TRAIN_SPLIT, args.window_size, args.stride)
    val = build_split_features(dataset_root, VAL_SPLIT, args.window_size, args.stride)
    test = build_split_features(dataset_root, TEST_SPLIT, args.window_size, args.stride)

    model = fit_logistic_model(train["X"], train["y"], args.epochs, args.learning_rate, args.l2)
    val_prob = predict_probability_matrix(model, val["X"])
    model["threshold"] = choose_threshold(val["y"], val_prob)

    train_metrics = evaluate_split(train, model)
    val_metrics = evaluate_split(val, model)
    test_metrics = evaluate_split(test, model)

    metrics = {
        "model_id": MODEL_ID,
        "train": train_metrics,
        "val": val_metrics,
        "test": test_metrics,
        "window_counts": {
            "train": int(len(train["y"])),
            "val": int(len(val["y"])),
            "test": int(len(test["y"])),
        },
    }
    metrics_summary = {
        "test_accuracy": test_metrics["accuracy"],
        "test_macro_f1": test_metrics["macro_f1"],
        "test_empty_recall": test_metrics["per_label"]["empty"]["recall"],
        "test_occupied_recall": test_metrics["per_label"]["occupied"]["recall"],
    }

    model_payload = {
        "model_type": "logistic_regression_numpy",
        "feature_names": FEATURE_NAMES,
        "feature_mean": model["mean"].tolist(),
        "feature_scale": model["scale"].tolist(),
        "weights": model["weights"].tolist(),
        "bias": float(model["bias"]),
        "threshold": float(model["threshold"]),
        "labels": ["empty", "occupied"],
    }
    with (artifact_dir / "model.pkl").open("wb") as handle:
        pickle.dump(model_payload, handle)

    write_json(artifact_dir / "metadata.json", build_metadata(args, metrics_summary))
    write_feature_config(artifact_dir / "feature_config.yaml", args.window_size, args.stride)
    write_json(
        artifact_dir / "label_mapping.json",
        {"empty": 0, "occupied": 1, "source_mapping": SOURCE_MAPPING},
    )
    write_json(artifact_dir / "metrics.json", metrics)

    print(json.dumps({"artifact_dir": str(artifact_dir), "test": test_metrics}, indent=2))
    return 0


def build_split_features(dataset_root: Path, groups: List[str], window_size: int, stride: int) -> Dict[str, Any]:
    rows: List[List[float]] = []
    labels: List[int] = []
    source_labels: List[str] = []
    group_ids: List[str] = []

    for group_id in groups:
        group_dir = dataset_root / group_id
        scenario = load_scenario_labels(group_dir / "scenario.md")
        baseline = build_empty_baseline(group_dir, scenario)
        for session_id, source_label in scenario.items():
            if source_label not in SOURCE_MAPPING:
                continue
            csv_path = find_session_csv(group_dir, session_id)
            if csv_path is None:
                continue
            matrix = normalized_log_amplitude(csv_path, baseline)
            binary_label = 0 if SOURCE_MAPPING[source_label] == "empty" else 1
            for start in range(0, max(0, len(matrix) - window_size + 1), stride):
                window = matrix[start : start + window_size]
                rows.append(window_features(window))
                labels.append(binary_label)
                source_labels.append(source_label)
                group_ids.append(group_id)

    if not rows:
        raise RuntimeError(f"no windows built for groups: {groups}")
    return {
        "X": np.asarray(rows, dtype=np.float64),
        "y": np.asarray(labels, dtype=np.int64),
        "source_labels": source_labels,
        "group_ids": group_ids,
    }


def load_scenario_labels(path: Path) -> Dict[str, str]:
    labels: Dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 3:
            continue
        session_id, label = cells[0], cells[2]
        if session_id.upper().startswith("S") and label in SOURCE_MAPPING:
            labels[session_id.upper()] = label
    return labels


def find_session_csv(group_dir: Path, session_id: str) -> Path | None:
    for name in (f"{session_id}.csv", f"{session_id.lower()}.csv"):
        path = group_dir / name
        if path.exists():
            return path
    return None


def build_empty_baseline(group_dir: Path, scenario: Dict[str, str]) -> Tuple[np.ndarray, np.ndarray]:
    empty_parts = []
    for session_id, label in scenario.items():
        if label != "empty":
            continue
        csv_path = find_session_csv(group_dir, session_id)
        if csv_path is None:
            continue
        amp = load_amplitude_csv(csv_path)
        empty_parts.append(np.log1p(amp).astype(np.float64))
    if not empty_parts:
        raise RuntimeError(f"no empty sessions available for baseline in {group_dir}")
    empty = np.concatenate(empty_parts, axis=0)
    center = np.nanmedian(empty, axis=0)
    mad = np.nanmedian(np.abs(empty - center), axis=0)
    scale = 1.4826 * mad + 1e-6
    return center, scale


def normalized_log_amplitude(csv_path: Path, baseline: Tuple[np.ndarray, np.ndarray]) -> np.ndarray:
    center, scale = baseline
    amp = load_amplitude_csv(csv_path).astype(np.float64)
    x = np.log1p(amp)
    z = (x - center) / scale
    return np.clip(z, -5.0, 5.0)


def window_features(window: np.ndarray) -> List[float]:
    flat_abs = np.abs(window).reshape(-1)
    return [
        float(np.mean(flat_abs)),
        float(np.max(flat_abs)),
        float(np.std(window)),
        float(np.sqrt(np.mean(flat_abs * flat_abs))),
        float(np.percentile(flat_abs, 95)),
        float(np.mean(flat_abs > 1.0)),
        float(np.mean(flat_abs > 2.0)),
        float(np.std(np.mean(window, axis=1))),
        float(np.mean(np.std(window, axis=0))),
    ]


def fit_logistic_model(X: np.ndarray, y: np.ndarray, epochs: int, lr: float, l2: float) -> Dict[str, Any]:
    mean = X.mean(axis=0)
    scale = X.std(axis=0) + 1e-6
    Xn = (X - mean) / scale
    weights = np.zeros(Xn.shape[1], dtype=np.float64)
    bias = 0.0
    pos = max(1, int(y.sum()))
    neg = max(1, int(len(y) - y.sum()))
    sample_weight = np.where(y == 1, len(y) / (2.0 * pos), len(y) / (2.0 * neg))

    for _ in range(epochs):
        logits = np.clip(Xn @ weights + bias, -60.0, 60.0)
        pred = 1.0 / (1.0 + np.exp(-logits))
        error = (pred - y) * sample_weight
        grad_w = (Xn.T @ error) / len(y) + l2 * weights
        grad_b = float(np.mean(error))
        weights -= lr * grad_w
        bias -= lr * grad_b
    return {"mean": mean, "scale": scale, "weights": weights, "bias": bias, "threshold": 0.5}


def predict_probability_matrix(model: Dict[str, Any], X: np.ndarray) -> np.ndarray:
    Xn = (X - model["mean"]) / model["scale"]
    logits = np.clip(Xn @ model["weights"] + model["bias"], -60.0, 60.0)
    return 1.0 / (1.0 + np.exp(-logits))


def choose_threshold(y_true: np.ndarray, probability: np.ndarray) -> float:
    candidates = sorted(set(float(value) for value in probability) | {0.5})
    best_threshold = 0.5
    best_macro_f1 = -1.0
    for threshold in candidates:
        pred = (probability >= threshold).astype(np.int64)
        metrics = classification_metrics(y_true, pred, [], [])
        macro_f1 = float(metrics["macro_f1"])
        if macro_f1 > best_macro_f1:
            best_macro_f1 = macro_f1
            best_threshold = threshold
    return best_threshold


def evaluate_split(split: Dict[str, Any], model: Dict[str, Any]) -> Dict[str, Any]:
    probability = predict_probability_matrix(model, split["X"])
    pred = (probability >= model["threshold"]).astype(np.int64)
    return classification_metrics(split["y"], pred, split["source_labels"], split["group_ids"])


def classification_metrics(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    source_labels: Iterable[str],
    group_ids: Iterable[str],
) -> Dict[str, Any]:
    del group_ids
    tn = int(((y_true == 0) & (y_pred == 0)).sum())
    fp = int(((y_true == 0) & (y_pred == 1)).sum())
    fn = int(((y_true == 1) & (y_pred == 0)).sum())
    tp = int(((y_true == 1) & (y_pred == 1)).sum())

    empty = per_label_metrics(tn, fn, fp)
    occupied = per_label_metrics(tp, fp, fn)
    source_recall: Dict[str, float | None] = {}
    for source_label in ["static_person", "moving_person"]:
        idx = np.asarray([label == source_label for label in source_labels], dtype=bool)
        if not idx.any():
            source_recall[source_label] = None
        else:
            source_recall[source_label] = float(np.mean(y_pred[idx] == 1))

    return {
        "accuracy": float((tp + tn) / max(1, len(y_true))),
        "macro_f1": float((empty["f1"] + occupied["f1"]) / 2.0),
        "per_label": {"empty": empty, "occupied": occupied},
        "confusion_matrix": {"labels": ["empty", "occupied"], "matrix": [[tn, fp], [fn, tp]]},
        "static_person_recall": source_recall.get("static_person"),
        "moving_person_recall": source_recall.get("moving_person"),
    }


def per_label_metrics(true_positive: int, false_positive: int, false_negative: int) -> Dict[str, float]:
    precision = true_positive / max(1, true_positive + false_positive)
    recall = true_positive / max(1, true_positive + false_negative)
    f1 = 0.0 if precision + recall == 0 else 2.0 * precision * recall / (precision + recall)
    return {"precision": float(precision), "recall": float(recall), "f1": float(f1)}


def build_metadata(args: argparse.Namespace, metrics_summary: Dict[str, float]) -> Dict[str, Any]:
    return {
        "model_id": MODEL_ID,
        "task": "occupancy_detection",
        "labels": ["empty", "occupied"],
        "input_type": "window_matrix",
        "input_shape": [args.window_size, 192],
        "checkpoint_file": "model.pkl",
        "preprocess_version": PREPROCESS_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "feature_version": FEATURE_VERSION,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "train_split": TRAIN_SPLIT,
        "val_split": VAL_SPLIT,
        "test_split": TEST_SPLIT,
        "excluded_groups": EXCLUDED_GROUPS,
        "source_training_code": "model_server/scripts/train_stage1_model.py",
        "metrics_summary": metrics_summary,
    }


def write_feature_config(path: Path, window_size: int, stride: int) -> None:
    path.write_text(
        "\n".join(
            [
                "raw_csi_length: 384",
                "representation: real_imag_interleaved",
                "amplitude: sqrt_iq",
                "log_transform: log1p",
                f"normalization: {NORMALIZATION_VERSION}",
                "baseline_source: empty_sessions_per_room_group",
                "center: median",
                "scale: mad",
                "clip_range: [-5.0, 5.0]",
                f"window_size: {window_size}",
                f"stride: {stride}",
                f"output_shape: [{window_size}, 192]",
                "",
            ]
        ),
        encoding="utf-8",
    )


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
