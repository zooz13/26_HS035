"""Baseline state loading, validation, and hot reload."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np


LOGGER = logging.getLogger(__name__)

WINDOW_SIZE = 128
STRIDE = 64
STAGE2_NORMALIZATION = "packet_mean_normalized"
DEFAULT_SEGMENT = "full_buffer"
SEGMENTS = {
    "full_buffer": (0, 192),
    "official_LLTF": (0, 64),
    "official_HT-LTF": (64, 192),
}


@dataclass
class BaselineState:
    baseline_id: str
    segment: str
    preprocessing_type: str
    window_size: int
    stride: int
    median: np.ndarray
    robust_scale: np.ndarray
    diff_abs_p95_scale: float
    temporal_std_p95_scale: float
    baseline_amp_mean: np.ndarray
    baseline_amp_median: np.ndarray
    baseline_amp_std: np.ndarray
    baseline_shape: np.ndarray
    num_windows: int
    duration_sec: float
    window_duration_sec: float

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "baseline_id": self.baseline_id,
            "segment": self.segment,
            "preprocessing_type": self.preprocessing_type,
            "window_size": self.window_size,
            "stride": self.stride,
            "median": self.median.tolist(),
            "robust_scale": self.robust_scale.tolist(),
            "diff_abs_p95_scale": self.diff_abs_p95_scale,
            "temporal_std_p95_scale": self.temporal_std_p95_scale,
            "baseline_amp_mean": self.baseline_amp_mean.tolist(),
            "baseline_amp_median": self.baseline_amp_median.tolist(),
            "baseline_amp_std": self.baseline_amp_std.tolist(),
            "baseline_shape": self.baseline_shape.tolist(),
            "num_windows": self.num_windows,
            "duration_sec": self.duration_sec,
            "window_duration_sec": self.window_duration_sec,
        }

    @classmethod
    def from_json_dict(cls, data: dict[str, Any]) -> "BaselineState":
        return cls(
            baseline_id=str(data["baseline_id"]),
            segment=str(data.get("segment", DEFAULT_SEGMENT)),
            preprocessing_type=str(
                data.get("preprocessing_type", STAGE2_NORMALIZATION)
            ),
            window_size=int(data.get("window_size", WINDOW_SIZE)),
            stride=int(data.get("stride", STRIDE)),
            median=np.asarray(data["median"], dtype=np.float32),
            robust_scale=np.asarray(data["robust_scale"], dtype=np.float32),
            diff_abs_p95_scale=float(data["diff_abs_p95_scale"]),
            temporal_std_p95_scale=float(data["temporal_std_p95_scale"]),
            baseline_amp_mean=np.asarray(
                data["baseline_amp_mean"], dtype=np.float32
            ),
            baseline_amp_median=np.asarray(
                data["baseline_amp_median"], dtype=np.float32
            ),
            baseline_amp_std=np.asarray(
                data["baseline_amp_std"], dtype=np.float32
            ),
            baseline_shape=np.asarray(
                data["baseline_shape"], dtype=np.float32
            ),
            num_windows=int(data.get("num_windows", 0)),
            duration_sec=float(data.get("duration_sec", 0.0)),
            window_duration_sec=float(data.get("window_duration_sec", 0.0)),
        )

    def as_scoring_baseline(self) -> dict[str, Any]:
        return {
            "median": self.median,
            "robust_scale": self.robust_scale,
            "diff_abs_p95_scale": self.diff_abs_p95_scale,
            "temporal_std_p95_scale": self.temporal_std_p95_scale,
            "baseline_amp_mean": self.baseline_amp_mean,
            "baseline_amp_median": self.baseline_amp_median,
            "baseline_amp_std": self.baseline_amp_std,
            "baseline_shape": self.baseline_shape,
        }


def load_baseline_state(path: str | Path) -> BaselineState:
    state = BaselineState.from_json_dict(
        json.loads(Path(path).read_text(encoding="utf-8"))
    )
    validate_baseline_state(state)
    return state


def save_baseline_state(path: str | Path, state: BaselineState) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(
                state.to_json_dict(),
                temporary,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        temporary_path.replace(destination)
    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise
    return destination


def validate_baseline_state(state: BaselineState) -> None:
    if state.segment not in SEGMENTS:
        raise ValueError(f"unknown baseline segment: {state.segment!r}")
    if state.window_size != WINDOW_SIZE:
        raise ValueError(
            f"baseline window_size must be {WINDOW_SIZE}, "
            f"got {state.window_size}"
        )
    expected_width = SEGMENTS[state.segment][1] - SEGMENTS[state.segment][0]
    arrays = {
        "median": state.median,
        "robust_scale": state.robust_scale,
        "baseline_amp_mean": state.baseline_amp_mean,
        "baseline_amp_median": state.baseline_amp_median,
        "baseline_amp_std": state.baseline_amp_std,
        "baseline_shape": state.baseline_shape,
    }
    for name, value in arrays.items():
        if value.shape != (expected_width,):
            raise ValueError(
                f"baseline {name} must have shape [{expected_width}], "
                f"got {list(value.shape)}"
            )
        if not np.isfinite(value).all():
            raise ValueError(f"baseline {name} contains NaN or infinite values")
    if np.any(state.robust_scale <= 0):
        raise ValueError("baseline robust_scale must contain positive values")
    if not np.isfinite(state.diff_abs_p95_scale):
        raise ValueError("baseline diff_abs_p95_scale must be finite")
    if not np.isfinite(state.temporal_std_p95_scale):
        raise ValueError("baseline temporal_std_p95_scale must be finite")
    if state.diff_abs_p95_scale <= 0:
        raise ValueError("baseline diff_abs_p95_scale must be positive")
    if state.temporal_std_p95_scale <= 0:
        raise ValueError("baseline temporal_std_p95_scale must be positive")


class BaselineStateManager:
    """Reload a baseline periodically and whenever its mtime changes."""

    def __init__(
        self,
        path: Path,
        expected_baseline_id: str,
        reload_sec: float = 10.0,
        clock: Callable[[], float] = time.monotonic,
        status_callback: Callable[..., None] | None = None,
    ) -> None:
        if reload_sec <= 0:
            raise ValueError("reload_sec must be greater than zero")
        self.path = Path(path)
        self.expected_baseline_id = expected_baseline_id
        self.reload_sec = reload_sec
        self.clock = clock
        self.status_callback = status_callback
        self.state: BaselineState | None = None
        self._last_check_at: float | None = None
        self._loaded_mtime_ns: int | None = None

    def refresh(self, *, force: bool = False) -> BaselineState | None:
        now = self.clock()
        if (
            not force
            and self._last_check_at is not None
            and now - self._last_check_at < self.reload_sec
        ):
            return self.state
        self._last_check_at = now

        try:
            mtime_ns = self.path.stat().st_mtime_ns
        except OSError as exc:
            self.state = None
            self._loaded_mtime_ns = None
            LOGGER.warning(
                "baseline state unavailable",
                extra={
                    "event": "baseline_state_unavailable",
                    "baseline_state_path": str(self.path),
                    "error": str(exc),
                },
            )
            self._notify(
                "baseline_state_unavailable",
                error=str(exc),
            )
            return None

        if (
            self.state is not None
            and self._loaded_mtime_ns == mtime_ns
            and not force
        ):
            return self.state

        try:
            state = load_baseline_state(self.path)
            if state.baseline_id != self.expected_baseline_id:
                raise ValueError(
                    "baseline_id mismatch: "
                    f"expected {self.expected_baseline_id!r}, "
                    f"got {state.baseline_id!r}"
                )
        except Exception as exc:
            actual_baseline_id = getattr(locals().get("state"), "baseline_id", None)
            self.state = None
            self._loaded_mtime_ns = None
            LOGGER.error(
                "baseline state invalid",
                extra={
                    "event": "baseline_state_invalid",
                    "expected_baseline_id": self.expected_baseline_id,
                    "actual_baseline_id": actual_baseline_id,
                    "baseline_state_path": str(self.path),
                    "error": str(exc),
                },
            )
            self._notify(
                "baseline_state_invalid",
                expected_baseline_id=self.expected_baseline_id,
                actual_baseline_id=actual_baseline_id,
                error=str(exc),
            )
            return None

        self.state = state
        self._loaded_mtime_ns = mtime_ns
        LOGGER.info(
            "baseline state loaded",
            extra={
                "event": "baseline_state_loaded",
                "baseline_id": state.baseline_id,
                "baseline_state_path": str(self.path),
                "mtime_ns": mtime_ns,
            },
        )
        self._notify(
            "baseline_state_loaded",
            baseline_id=state.baseline_id,
        )
        return state

    def _notify(self, event: str, **values: Any) -> None:
        if self.status_callback is None:
            return
        try:
            self.status_callback(event, **values)
        except Exception:
            LOGGER.warning(
                "baseline status callback failed",
                extra={
                    "event": "status_callback_failed",
                    "source": "baseline_state",
                    "status_event": event,
                },
            )
