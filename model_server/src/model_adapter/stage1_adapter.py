from __future__ import annotations

import json
import math
import pickle
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

from common.labels import STAGE1_EMPTY, STAGE1_OCCUPIED
from common.types import ProcessedWindow, StageResult


class Stage1AdapterError(ValueError):
    pass


class Stage1Adapter:
    def __init__(self, artifact_path: str | Path):
        self.artifact_path = Path(artifact_path)
        self.metadata: Dict[str, Any] = {}
        self.label_mapping: Dict[str, Any] = {}
        self.model: Dict[str, Any] = {}
        self.threshold_config: Dict[str, Any] = {}
        self.calibration_policy: Dict[str, Any] = {}
        self.adapter_mode = "unloaded"
        self.loaded = False

    def load(self) -> None:
        metadata_path = self.artifact_path / "metadata.json"
        label_mapping_path = self.artifact_path / "label_mapping.json"
        model_path = self.artifact_path / "model.pkl"
        threshold_config_path = self.artifact_path / "stage1_threshold_config.json"
        calibration_policy_path = self.artifact_path / "calibration_policy.json"

        if not metadata_path.exists():
            raise Stage1AdapterError(f"missing metadata.json at {metadata_path}")
        if not label_mapping_path.exists():
            raise Stage1AdapterError(f"missing label_mapping.json at {label_mapping_path}")

        self.metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        self.label_mapping = json.loads(label_mapping_path.read_text(encoding="utf-8"))
        if threshold_config_path.exists():
            self.threshold_config = json.loads(threshold_config_path.read_text(encoding="utf-8"))
            if calibration_policy_path.exists():
                self.calibration_policy = json.loads(calibration_policy_path.read_text(encoding="utf-8"))
            self.adapter_mode = "threshold"
        elif model_path.exists():
            with model_path.open("rb") as handle:
                self.model = pickle.load(handle)
            self.adapter_mode = "legacy_logreg"
        else:
            raise Stage1AdapterError(f"missing threshold config or model.pkl at {self.artifact_path}")
        self.loaded = True

    def predict(self, processed_window: ProcessedWindow) -> StageResult:
        if not self.loaded:
            self.load()
        self._validate_window_contract(processed_window)
        if self.adapter_mode == "threshold":
            return self._predict_threshold(processed_window)
        features = extract_window_features(processed_window.data)
        probability = self._predict_probability(features)
        threshold = float(self.model.get("threshold", 0.5))
        label = STAGE1_OCCUPIED if probability >= threshold else STAGE1_EMPTY
        confidence = probability if label == STAGE1_OCCUPIED else 1.0 - probability
        return StageResult(
            label=label,
            confidence=max(0.0, min(1.0, confidence)),
            score=probability,
            reason=f"stage1 checkpoint probability={probability:.6f}, threshold={threshold:.6f}",
        )

    def get_model_info(self) -> Dict[str, Any]:
        if not self.loaded:
            self.load()
        return {
            "model_id": self.metadata.get("model_id"),
            "artifact_path": str(self.artifact_path),
            "checkpoint_file": self.metadata.get("checkpoint_file"),
            "adapter_mode": self.adapter_mode,
            "stage1_mode": self.metadata.get("stage1_mode"),
            "input_shape": self.metadata.get("input_shape"),
            "preprocess_version": self.metadata.get("preprocess_version"),
            "normalization_version": self.metadata.get("normalization_version"),
            "feature_version": self.metadata.get("feature_version"),
        }

    def _validate_window_contract(self, window: ProcessedWindow) -> None:
        expected_shape = self.metadata.get("input_shape")
        if window.data_type not in {"window_matrix", "both"}:
            raise Stage1AdapterError("checkpoint Stage 1 requires data_type=window_matrix or both")
        if window.data is None:
            raise Stage1AdapterError("checkpoint Stage 1 requires ProcessedWindow.data")
        if expected_shape and list(window.shape) != list(expected_shape):
            raise Stage1AdapterError(f"shape mismatch: got {window.shape}, expected {expected_shape}")

        expected_preprocess = self.metadata.get("preprocess_version")
        if expected_preprocess and window.preprocess_version != expected_preprocess:
            raise Stage1AdapterError(
                f"preprocess_version mismatch: got {window.preprocess_version}, expected {expected_preprocess}"
            )

        expected_normalization = self.metadata.get("normalization_version")
        if expected_normalization and window.normalization_version != expected_normalization:
            raise Stage1AdapterError(
                "normalization_version mismatch: "
                f"got {window.normalization_version}, expected {expected_normalization}"
            )

        expected_feature = self.metadata.get("feature_version")
        if expected_feature and window.feature_version != expected_feature:
            raise Stage1AdapterError(f"feature_version mismatch: got {window.feature_version}, expected {expected_feature}")

    def _predict_threshold(self, processed_window: ProcessedWindow) -> StageResult:
        features = extract_threshold_features(processed_window.data)
        score_name = str(self.threshold_config.get("score_name", "server_window_score"))
        if score_name == "server_window_score":
            score = server_window_score(features)
        else:
            if score_name not in features:
                raise Stage1AdapterError(f"threshold score {score_name!r} is not available")
            score = float(features[score_name])
        threshold = float(self.threshold_config.get("selected_threshold", self.threshold_config.get("threshold", 0.0)))
        label = STAGE1_OCCUPIED if score >= threshold else STAGE1_EMPTY
        confidence = threshold_confidence(score, threshold, label)
        return StageResult(
            label=label,
            confidence=confidence,
            score=score,
            reason=f"stage1 threshold score={score:.6f}, threshold={threshold:.6f}",
        )

    def _predict_probability(self, features: Dict[str, float]) -> float:
        feature_names: List[str] = list(self.model["feature_names"])
        means: List[float] = list(self.model["feature_mean"])
        scales: List[float] = list(self.model["feature_scale"])
        weights: List[float] = list(self.model["weights"])
        bias = float(self.model["bias"])

        logit = bias
        for idx, name in enumerate(feature_names):
            value = float(features[name])
            normalized = (value - means[idx]) / scales[idx]
            logit += weights[idx] * normalized
        return 1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, logit))))


def extract_window_features(window_data: Any) -> Dict[str, float]:
    matrix = _to_matrix(window_data)
    flat = [abs(value) for row in matrix for value in row]
    if not flat:
        raise Stage1AdapterError("window data is empty")

    rows = len(matrix)
    cols = len(matrix[0]) if rows else 0
    mean_abs = sum(flat) / len(flat)
    rms = math.sqrt(sum(value * value for value in flat) / len(flat))
    sorted_flat = sorted(flat)
    max_abs = sorted_flat[-1]
    p95_abs = _percentile(sorted_flat, 0.95)
    frac_abs_gt_1 = sum(1 for value in flat if value > 1.0) / len(flat)
    frac_abs_gt_2 = sum(1 for value in flat if value > 2.0) / len(flat)

    signed_flat = [value for row in matrix for value in row]
    std = _std(signed_flat)
    row_means = [sum(row) / len(row) for row in matrix if row]
    temporal_std_mean = _std(row_means)

    carrier_stds: List[float] = []
    if cols:
        for col_idx in range(cols):
            carrier_stds.append(_std([matrix[row_idx][col_idx] for row_idx in range(rows)]))
    carrier_std_mean = sum(carrier_stds) / len(carrier_stds) if carrier_stds else 0.0

    return {
        "mean_abs": mean_abs,
        "max_abs": max_abs,
        "std": std,
        "rms": rms,
        "p95_abs": p95_abs,
        "frac_abs_gt_1": frac_abs_gt_1,
        "frac_abs_gt_2": frac_abs_gt_2,
        "temporal_std_mean": temporal_std_mean,
        "carrier_std_mean": carrier_std_mean,
    }


def extract_threshold_features(window_data: Any) -> Dict[str, float]:
    matrix = _to_matrix(window_data)
    rows = len(matrix)
    cols = len(matrix[0]) if rows else 0
    flat_abs = sorted(abs(value) for row in matrix for value in row)
    if not flat_abs:
        raise Stage1AdapterError("window data is empty")
    carrier_stds = []
    carrier_p90 = []
    if cols:
        for col_idx in range(cols):
            values = [matrix[row_idx][col_idx] for row_idx in range(rows)]
            carrier_stds.append(_std(values))
            carrier_p90.append(_percentile(sorted(abs(value) for value in values), 0.90))
    diffs = []
    for row_idx in range(1, rows):
        prev = matrix[row_idx - 1]
        curr = matrix[row_idx]
        diffs.extend(abs(curr[col_idx] - prev[col_idx]) for col_idx in range(cols))
    sorted_diffs = sorted(diffs)
    return {
        "robust_z_p90_abs": _percentile(flat_abs, 0.90),
        "robust_z_p95_abs": _percentile(flat_abs, 0.95),
        "robust_z_max_abs": flat_abs[-1],
        "temporal_std_mean": sum(carrier_stds) / len(carrier_stds) if carrier_stds else 0.0,
        "carrier_p90_mean": sum(carrier_p90) / len(carrier_p90) if carrier_p90 else 0.0,
        "diff_abs_mean": sum(sorted_diffs) / len(sorted_diffs) if sorted_diffs else 0.0,
        "diff_abs_p95": _percentile(sorted_diffs, 0.95) if sorted_diffs else 0.0,
    }


def server_window_score(features: Dict[str, float]) -> float:
    return max(
        float(features["robust_z_p90_abs"]),
        float(features["temporal_std_mean"]),
        float(features["diff_abs_p95"]),
    )


def threshold_confidence(score: float, threshold: float, label: str) -> float:
    scale = max(abs(threshold), 1.0)
    distance = abs(float(score) - float(threshold)) / scale
    confidence = 0.5 + min(0.49, distance * 0.5)
    if label not in {STAGE1_EMPTY, STAGE1_OCCUPIED}:
        return 0.0
    return float(max(0.0, min(0.99, confidence)))


def _to_matrix(value: Any) -> List[List[float]]:
    if not isinstance(value, list) or not value:
        raise Stage1AdapterError("window data must be a non-empty nested list")
    matrix: List[List[float]] = []
    width = None
    for row in value:
        if not isinstance(row, list) or not row:
            raise Stage1AdapterError("window data rows must be non-empty lists")
        values = [float(item) for item in row]
        if width is None:
            width = len(values)
        elif len(values) != width:
            raise Stage1AdapterError("window data rows must have a consistent width")
        matrix.append(values)
    return matrix


def _percentile(sorted_values: Sequence[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    pos = (len(sorted_values) - 1) * q
    lower = int(math.floor(pos))
    upper = int(math.ceil(pos))
    if lower == upper:
        return sorted_values[lower]
    weight = pos - lower
    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight


def _std(values: Iterable[float]) -> float:
    items = list(values)
    if not items:
        return 0.0
    mean = sum(items) / len(items)
    return math.sqrt(sum((value - mean) ** 2 for value in items) / len(items))
