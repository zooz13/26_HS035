"""Payload builders for room, baseline, and calibration setup APIs."""

from __future__ import annotations

from typing import Any

from app.baseline_state import BaselineState
from app.csi_preprocessor import (
    NORMALIZATION_VERSION,
    PREPROCESS_VERSION,
    STAGE1_FEATURE_VERSION,
)


def build_room_create_payload(
    room_id: str,
    room_name: str,
    device_group_id: str,
    description: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "room_id": room_id,
        "room_name": room_name,
        "device_group_id": device_group_id,
    }
    if description:
        payload["description"] = description
    return payload


def build_baseline_start_payload(
    duration_sec: float,
    window_duration_sec: float,
) -> dict[str, Any]:
    return {
        "duration_sec": float(duration_sec),
        "window_duration_sec": float(window_duration_sec),
        "mode": "empty",
        "requested_by": "gateway",
    }


def build_baseline_finish_payload(
    baseline_session_id: str,
    baseline_state: BaselineState,
    summary: dict[str, float],
) -> dict[str, Any]:
    return {
        "baseline_session_id": baseline_session_id,
        "baseline_id": baseline_state.baseline_id,
        "duration_sec": baseline_state.duration_sec,
        "window_duration_sec": baseline_state.window_duration_sec,
        "num_windows": baseline_state.num_windows,
        "preprocess_version": PREPROCESS_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "feature_version": STAGE1_FEATURE_VERSION,
        "baseline_profile": {
            "owner": "gateway_preprocessor",
            "segment": baseline_state.segment,
            "subcarrier_count": len(baseline_state.median),
            "window_size": baseline_state.window_size,
            "stride": baseline_state.stride,
            "preprocessing_type": baseline_state.preprocessing_type,
            "summary": summary,
        },
    }


def build_transport_test_baseline_finish_payload(
    baseline_session_id: str,
    baseline_id: str,
    window_duration_sec: float,
) -> dict[str, Any]:
    return {
        "baseline_session_id": baseline_session_id,
        "baseline_id": baseline_id,
        "duration_sec": 0.0,
        "window_duration_sec": float(window_duration_sec),
        "num_windows": 0,
        "preprocess_version": PREPROCESS_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "feature_version": STAGE1_FEATURE_VERSION,
        "baseline_profile": {
            "owner": "gateway_transport_test",
            "preprocessing_type": NORMALIZATION_VERSION,
            "summary": {
                "transport_test": True,
                "note": "No Stage-1 baseline statistics are used.",
            },
        },
    }


def build_calibration_start_payload(
    baseline_id: str,
    expected_windows: int,
    window_duration_sec: float,
    method: str = "guided_baseline_merge",
) -> dict[str, Any]:
    return {
        "baseline_id": baseline_id,
        "method": method,
        "expected_windows": int(expected_windows),
        "window_duration_sec": float(window_duration_sec),
    }


def build_calibration_window_payload(
    calibration_session_id: str,
    window_id: str,
    baseline_id: str,
    features: dict[str, float],
) -> dict[str, Any]:
    return {
        "calibration_session_id": calibration_session_id,
        "window_id": window_id,
        "baseline_id": baseline_id,
        "feature_version": STAGE1_FEATURE_VERSION,
        "features": {
            "static_score": float(features["static_score"]),
            "motion_score": float(features["motion_score"]),
            "shape_score": float(features["shape_score"]),
            "robust_max_shape_score": float(
                features["robust_max_shape_score"]
            ),
        },
    }


def build_calibration_finish_payload(
    calibration_session_id: str,
    method: str = "guided_baseline_merge",
    quantile: float = 0.95,
    margin: float = 0.0,
) -> dict[str, Any]:
    return {
        "calibration_session_id": calibration_session_id,
        "method": method,
        "quantile": float(quantile),
        "margin": float(margin),
    }


def build_finish_envelope(
    *,
    operation: str,
    room_id: str,
    server_url: str,
    payload: dict[str, Any],
    candidate_baseline_state_path: str | None = None,
) -> dict[str, Any]:
    if operation not in {"baseline_finish", "calibration_finish"}:
        raise ValueError(f"unsupported finish operation: {operation}")
    envelope = {
        "operation": operation,
        "room_id": room_id,
        "server_url": server_url.rstrip("/"),
        "payload": payload,
    }
    if candidate_baseline_state_path is not None:
        envelope["candidate_baseline_state_path"] = (
            candidate_baseline_state_path
        )
    return envelope
