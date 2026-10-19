from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any, Iterable

import numpy as np

from common.types import (
    BaselineProfile,
    CalibrationFinishRequest,
    CalibrationProfile,
    CalibrationSession,
    CalibrationStartRequest,
    CalibrationWindow,
    CalibrationWindowRequest,
)
from storage.state_store import StateStore


class CalibrationManagerError(ValueError):
    pass


class CalibrationManager:
    def __init__(
        self,
        store: StateStore,
        stage1_artifact_id: str,
        global_threshold: float,
        score_name: str = "robust_max_shape_score",
        empty_quantile: float = 0.95,
        empty_quantile_margin: float = 0.0,
    ):
        self.store = store
        self.stage1_artifact_id = stage1_artifact_id
        self.global_threshold = float(global_threshold)
        self.score_name = score_name
        self.empty_quantile = validate_quantile(empty_quantile)
        self.empty_quantile_margin = validate_finite_number(
            empty_quantile_margin,
            "empty quantile margin",
        )

    def start_calibration(self, room_id: str, request: CalibrationStartRequest) -> CalibrationSession:
        room = require_room(self.store, room_id)
        if room.baseline_id != request.baseline_id:
            raise CalibrationManagerError("baseline_id does not match the room baseline")
        if room.status not in {"baseline_ready", "ready", "running"}:
            raise CalibrationManagerError("room baseline must be ready before calibration")
        if request.method not in {"guided_baseline_merge", "empty_quantile_threshold", "no_calibration"}:
            raise CalibrationManagerError("unsupported calibration method")
        if request.expected_windows <= 0 and request.method != "no_calibration":
            raise CalibrationManagerError("expected_windows must be positive")
        calibration_session_id = f"calib_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        session = CalibrationSession(
            calibration_session_id=calibration_session_id,
            room_id=room_id,
            baseline_id=request.baseline_id,
            method=request.method,
            expected_windows=request.expected_windows,
            window_duration_sec=request.window_duration_sec,
            status="calibration_collecting",
            created_at=utc_now(),
            windows=[],
        )
        self.store.save_calibration_session(session)
        self.store.save_room(room.copy(update={"status": "calibration_collecting", "updated_at": utc_now()}))
        return session

    def create_default_calibration_profile(self, room_id: str, baseline_id: str) -> CalibrationProfile:
        room = require_room(self.store, room_id)
        if room.baseline_id != baseline_id:
            raise CalibrationManagerError("baseline_id does not match the room baseline")
        baseline = self.store.get_baseline_profile(baseline_id)
        if baseline is None:
            raise CalibrationManagerError("baseline profile not found")
        profile = self.build_default_calibration_profile(room_id, baseline)
        self.store.save_calibration_profile(profile)
        self.store.save_room(
            room.copy(
                update={
                    "status": "ready",
                    "calibration_id": profile.calibration_id,
                    "updated_at": utc_now(),
                }
            )
        )
        return profile

    def build_default_calibration_profile(
        self,
        room_id: str,
        baseline: BaselineProfile,
    ) -> CalibrationProfile:
        require_room(self.store, room_id)
        if baseline.room_id != room_id:
            raise CalibrationManagerError("baseline profile does not belong to the room")
        effective_threshold, summary_key, quantile_value = empty_quantile_threshold_from_summary(
            baseline.summary,
            quantile=self.empty_quantile,
            margin=self.empty_quantile_margin,
        )
        normalized_summary = normalize_baseline_score_summary(baseline.summary)
        return CalibrationProfile(
            calibration_id=next_default_calibration_id(self.store, room_id),
            room_id=room_id,
            baseline_id=baseline.baseline_id,
            stage1_artifact_id=self.stage1_artifact_id,
            method="empty_quantile_threshold",
            global_threshold=self.global_threshold,
            effective_threshold=effective_threshold,
            num_windows=baseline.num_windows,
            score_name=self.score_name,
            score_summary=normalized_summary,
            margin=self.empty_quantile_margin,
            source_metadata={
                "source": "baseline_finish_summary",
                "baseline_session_id": baseline.baseline_session_id,
                "quantile": self.empty_quantile,
                "summary_key": summary_key,
                "quantile_value": quantile_value,
            },
            status="ready",
            created_at=utc_now(),
        )

    def add_calibration_window(self, room_id: str, request: CalibrationWindowRequest) -> CalibrationSession:
        room = require_room(self.store, room_id)
        session = self.store.get_calibration_session(request.calibration_session_id)
        if session is None or session.room_id != room_id:
            raise CalibrationManagerError("calibration session not found for room")
        if room.baseline_id != request.baseline_id or session.baseline_id != request.baseline_id:
            raise CalibrationManagerError("baseline_id does not match the active calibration")
        baseline = self.store.get_baseline_profile(request.baseline_id)
        if baseline is None:
            raise CalibrationManagerError("baseline profile not found")
        if request.feature_version != baseline.feature_version:
            raise CalibrationManagerError("feature_version does not match the baseline profile")
        score_from_features(request.features, self.score_name)
        window = CalibrationWindow(**request.dict())
        updated = session.copy(update={"windows": list(session.windows) + [window]})
        self.store.save_calibration_session(updated)
        return updated

    def finish_calibration(self, room_id: str, request: CalibrationFinishRequest) -> CalibrationProfile:
        room = require_room(self.store, room_id)
        session = self.store.get_calibration_session(request.calibration_session_id)
        if session is None or session.room_id != room_id:
            raise CalibrationManagerError("calibration session not found for room")
        if request.method != session.method:
            raise CalibrationManagerError("finish method must match the active calibration method")
        if len(session.windows) < session.expected_windows:
            raise CalibrationManagerError("not enough calibration windows")
        scores = np.asarray([score_from_features(window.features, self.score_name) for window in session.windows], dtype=float)
        summary = score_summary(scores) if scores.size else {"p50": 0.0, "p90": 0.0, "p95": 0.0, "p99": 0.0}
        margin = float(request.margin)
        if request.method in {"guided_baseline_merge", "no_calibration"}:
            effective_threshold = self.global_threshold + margin
        elif request.method == "empty_quantile_threshold":
            effective_threshold = calculate_empty_quantile_threshold(
                scores,
                request.quantile,
                margin,
            )
        else:
            raise CalibrationManagerError("unsupported calibration method")
        calibration_id = next_calibration_id(self.store, room_id)
        profile = CalibrationProfile(
            calibration_id=calibration_id,
            room_id=room_id,
            baseline_id=session.baseline_id,
            stage1_artifact_id=self.stage1_artifact_id,
            method=request.method,
            global_threshold=self.global_threshold,
            effective_threshold=effective_threshold,
            num_windows=len(session.windows),
            score_name=self.score_name,
            score_summary=summary,
            margin=margin,
            source_metadata={
                "source": "calibration_windows",
                "calibration_session_id": session.calibration_session_id,
                "quantile": float(request.quantile),
            },
            status="ready",
            created_at=utc_now(),
        )
        self.store.save_calibration_profile(profile)
        self.store.save_calibration_session(session.copy(update={"status": "ready"}))
        self.store.save_room(
            room.copy(
                update={
                    "status": "ready",
                    "calibration_id": profile.calibration_id,
                    "updated_at": utc_now(),
                }
            )
        )
        return profile

    def get_active_calibration(self, room_id: str) -> CalibrationSession | None:
        for session in self.store.calibration_sessions.values():
            if session.room_id == room_id and session.status == "calibration_collecting":
                return session
        return None


def require_room(store: StateStore, room_id: str):
    room = store.get_room(room_id)
    if room is None:
        raise CalibrationManagerError(f"room not found: {room_id}")
    return room


def score_from_features(features: dict[str, float], score_name: str = "robust_max_shape_score") -> float:
    if score_name in features:
        return float(features[score_name])
    if "final_score" in features:
        return float(features["final_score"])
    required = ["static_score", "motion_score", "shape_score"]
    if all(name in features for name in required):
        return float(max(features[name] for name in required))
    raise CalibrationManagerError(f"features must include {score_name} or static/motion/shape scores")


def score_summary(scores: np.ndarray) -> dict[str, float]:
    if scores.size == 0:
        raise CalibrationManagerError("no calibration scores available")
    return {
        "p50": float(np.quantile(scores, 0.50)),
        "p90": float(np.quantile(scores, 0.90)),
        "p95": float(np.quantile(scores, 0.95)),
        "p99": float(np.quantile(scores, 0.99)),
    }


_SUMMARY_KEYS_BY_QUANTILE = {
    0.50: ("median_score", "p50"),
    0.90: ("p90_score", "p90"),
    0.95: ("p95_score", "p95"),
    0.99: ("p99_score", "p99"),
}


def calculate_empty_quantile_threshold(
    scores: Iterable[float] | np.ndarray,
    quantile: float,
    margin: float,
) -> float:
    """Apply the existing empty-score quantile policy to raw window scores."""
    validated_quantile = validate_quantile(quantile)
    validated_margin = validate_finite_number(margin, "empty quantile margin")
    values = np.asarray(list(scores) if not isinstance(scores, np.ndarray) else scores, dtype=float)
    if values.size == 0:
        raise CalibrationManagerError("no calibration scores available")
    if not np.isfinite(values).all():
        raise CalibrationManagerError("calibration scores must contain only finite numbers")
    quantile_value = float(np.quantile(values, validated_quantile))
    return apply_empty_quantile_margin(quantile_value, validated_margin)


def empty_quantile_threshold_from_summary(
    summary: dict[str, Any],
    quantile: float,
    margin: float,
) -> tuple[float, str, float]:
    """Reuse an exact precomputed quantile from a baseline finish summary."""
    validated_quantile = validate_quantile(quantile)
    validated_margin = validate_finite_number(margin, "empty quantile margin")
    if not isinstance(summary, dict):
        raise CalibrationManagerError("baseline_profile.summary must be an object")
    summary_keys = summary_keys_for_quantile(validated_quantile)
    for summary_key in summary_keys:
        if summary_key in summary:
            quantile_value = validate_finite_number(
                summary[summary_key],
                f"baseline_profile.summary.{summary_key}",
            )
            return (
                apply_empty_quantile_margin(quantile_value, validated_margin),
                summary_key,
                quantile_value,
            )
    expected = " or ".join(summary_keys)
    raise CalibrationManagerError(
        f"baseline_profile.summary must include the exact {validated_quantile:.2f} quantile as {expected}"
    )


def normalize_baseline_score_summary(summary: dict[str, Any]) -> dict[str, float]:
    normalized: dict[str, float] = {}
    for quantile, keys in _SUMMARY_KEYS_BY_QUANTILE.items():
        for key in keys:
            if key in summary:
                normalized[f"p{int(quantile * 100):02d}"] = validate_finite_number(
                    summary[key],
                    f"baseline_profile.summary.{key}",
                )
                break
    return normalized


def summary_keys_for_quantile(quantile: float) -> tuple[str, ...]:
    for supported, keys in _SUMMARY_KEYS_BY_QUANTILE.items():
        if math.isclose(quantile, supported, rel_tol=0.0, abs_tol=1e-12):
            return keys
    raise CalibrationManagerError(
        f"baseline summary does not provide an exact precomputed quantile for {quantile:.6g}"
    )


def apply_empty_quantile_margin(quantile_value: float, margin: float) -> float:
    threshold = quantile_value + margin
    if not math.isfinite(threshold):
        raise CalibrationManagerError("effective threshold must be finite")
    return threshold


def validate_quantile(value: float) -> float:
    quantile = validate_finite_number(value, "empty quantile")
    if quantile < 0.0 or quantile > 1.0:
        raise CalibrationManagerError("empty quantile must be between 0 and 1")
    return quantile


def validate_finite_number(value: Any, field_name: str) -> float:
    if isinstance(value, bool):
        raise CalibrationManagerError(f"{field_name} must be a finite number")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise CalibrationManagerError(f"{field_name} must be a finite number") from exc
    if not math.isfinite(parsed):
        raise CalibrationManagerError(f"{field_name} must be a finite number")
    return parsed


def next_calibration_id(store: StateStore, room_id: str) -> str:
    prefix = f"calib_{room_id}_v"
    existing = [key for key in store.calibration_profiles if key.startswith(prefix)]
    return f"{prefix}{len(existing) + 1:03d}"


def next_default_calibration_id(store: StateStore, room_id: str) -> str:
    prefix = f"calib_{room_id}_baseline_default_v"
    existing = [key for key in store.calibration_profiles if key.startswith(prefix)]
    return f"{prefix}{len(existing) + 1:03d}"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
