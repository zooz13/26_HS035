"""Build and validate realtime model-server payloads."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from app.csi_preprocessor import (
    DATA_TYPE_STAGE1,
    STAGE1_FEATURE_VERSION,
    validate_motion_input,
)


def build_window_id(room_id: str, window_start: float) -> str:
    timestamp = datetime.fromtimestamp(window_start, tz=timezone.utc)
    return f"{room_id}_{timestamp:%Y%m%dT%H%M%S}{timestamp.microsecond // 1000:03d}Z"


def build_realtime_window_payload(
    window_id: str,
    baseline_id: str,
    calibration_id: str,
    device_group_id: str,
    window_start_time: str,
    window_end_time: str,
    window_duration_sec: float,
    features: dict[str, float],
    motion_input: dict[str, Any],
) -> dict[str, Any]:
    validate_feature_payload(features)
    validate_motion_input(motion_input)
    payload = {
        "window_id": window_id,
        "baseline_id": baseline_id,
        "calibration_id": calibration_id,
        "device_group_id": device_group_id,
        "window_start_time": window_start_time,
        "window_end_time": window_end_time,
        "window_duration_sec": float(window_duration_sec),
        "data_type": DATA_TYPE_STAGE1,
        "feature_version": STAGE1_FEATURE_VERSION,
        "features": required_stage1_features(features),
        "motion_input": motion_input,
    }
    validate_window_payload(payload)
    return payload


def required_stage1_features(
    features: dict[str, float],
) -> dict[str, float]:
    return {
        "static_score": float(features["static_score"]),
        "motion_score": float(features["motion_score"]),
        "shape_score": float(features["shape_score"]),
        "robust_max_shape_score": float(
            features["robust_max_shape_score"]
        ),
    }


def validate_feature_payload(features: dict[str, Any]) -> None:
    required = [
        "static_score",
        "motion_score",
        "shape_score",
        "robust_max_shape_score",
    ]
    missing = [name for name in required if name not in features]
    if missing:
        raise ValueError(
            f"feature payload missing required keys: {missing}"
        )
    for name in required:
        value = float(features[name])
        if not math.isfinite(value):
            raise ValueError(
                f"feature {name} must be finite, got {value}"
            )


def validate_window_payload(payload: dict[str, Any]) -> None:
    if not payload.get("window_id"):
        raise ValueError("window_id must not be empty")
    if not payload.get("window_start_time"):
        raise ValueError("window_start_time must not be empty")
    if not payload.get("window_end_time"):
        raise ValueError("window_end_time must not be empty")
    validate_feature_payload(payload.get("features", {}))
    if payload.get("motion_input") is not None:
        validate_motion_input(payload["motion_input"])

