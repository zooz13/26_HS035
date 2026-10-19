from __future__ import annotations

import math
from typing import Any, Iterable, List, Sequence

from common.labels import (
    DATA_TYPE_BOTH,
    DATA_TYPE_FEATURE_VECTOR,
    DATA_TYPE_WINDOW_MATRIX,
    DATA_TYPES,
)
from common.types import ProcessedWindow


class ValidationError(ValueError):
    def __init__(self, errors: List[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def validate_processed_window(window: ProcessedWindow) -> None:
    errors: List[str] = []

    _require_text(window.window_id, "window_id", errors)
    _require_text(window.device_group_id, "device_group_id", errors)
    _require_text(window.start_time, "start_time", errors)
    _require_text(window.end_time, "end_time", errors)
    _require_text(window.preprocess_version, "preprocess_version", errors)
    _require_text(window.feature_version, "feature_version", errors)

    if not window.device_ids:
        errors.append("device_ids must not be empty")
    elif any(not isinstance(device_id, str) or not device_id.strip() for device_id in window.device_ids):
        errors.append("device_ids must contain non-empty strings")

    if window.data_type not in DATA_TYPES:
        errors.append(f"data_type must be one of {sorted(DATA_TYPES)}")

    if not window.shape:
        errors.append("shape must not be empty")
    elif any(not isinstance(dim, int) or dim <= 0 for dim in window.shape):
        errors.append("shape must contain positive integers")

    if window.data_type == DATA_TYPE_FEATURE_VECTOR and window.features is None:
        errors.append("features are required when data_type is feature_vector")
    if window.data_type == DATA_TYPE_WINDOW_MATRIX and window.data is None:
        errors.append("data is required when data_type is window_matrix")
    if window.data_type == DATA_TYPE_BOTH:
        if window.features is None:
            errors.append("features are required when data_type is both")
        if window.data is None:
            errors.append("data is required when data_type is both")

    if window.features is not None:
        if not _all_finite(window.features):
            errors.append("features must not contain NaN or Inf")
        if window.shape and len(window.shape) == 1 and len(window.features) != window.shape[0]:
            errors.append("features length must match shape for feature vectors")

    if window.data is not None:
        if not _all_finite(window.data):
            errors.append("data must not contain NaN or Inf")
        if window.shape and not _matches_shape(window.data, window.shape):
            errors.append("data nested lengths must match shape")

    if errors:
        raise ValidationError(errors)


def validate_processed_window_dict(payload: dict[str, Any]) -> ProcessedWindow:
    window = ProcessedWindow(**payload)
    validate_processed_window(window)
    return window


def _require_text(value: str, field_name: str, errors: List[str]) -> None:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{field_name} is required")


def _all_finite(value: Any) -> bool:
    if isinstance(value, bool):
        return True
    if isinstance(value, (int, float)):
        return math.isfinite(float(value))
    if isinstance(value, dict):
        return all(_all_finite(item) for item in value.values())
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
        return all(_all_finite(item) for item in value)
    return True


def _matches_shape(value: Any, shape: Sequence[int]) -> bool:
    if not shape:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if not isinstance(value, list) or len(value) != shape[0]:
        return False
    return all(_matches_shape(item, shape[1:]) for item in value)
