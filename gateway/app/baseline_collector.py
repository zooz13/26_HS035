"""Explicit empty-room baseline collection workflow."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from app.baseline_state import save_baseline_state
from app.config import GatewayConfig
from app.csi_preprocessor import (
    NORMALIZATION_VERSION,
    STAGE1_FEATURE_VERSION,
    baseline_score_summary,
    build_baseline_state,
    parse_raw_csi_packet,
)
from app.setup_api_client import SetupApiClient, SetupApiError
from app.setup_capture import SetupCaptureRuntime
from app.setup_payload_builder import (
    build_baseline_finish_payload,
    build_baseline_start_payload,
    build_finish_envelope,
)
from app.setup_spool import SetupSpoolStore
from app.setup_state import (
    SetupState,
    SetupStateStore,
    atomic_write_json,
    utc_now,
)


LOGGER = logging.getLogger(__name__)


class BaselineCollectionError(RuntimeError):
    pass


class BaselineCollector:
    def __init__(
        self,
        config: GatewayConfig,
        api_client: SetupApiClient,
        state_store: SetupStateStore,
        spool_store: SetupSpoolStore,
        *,
        capture_runtime: SetupCaptureRuntime | None = None,
    ) -> None:
        self.config = config
        self.api_client = api_client
        self.state_store = state_store
        self.spool_store = spool_store
        self.capture_runtime = capture_runtime

    def collect(self, duration_sec: float = 300.0) -> SetupState:
        start_response = self.api_client.baseline_start(
            self.config.room_id,
            build_baseline_start_payload(
                duration_sec,
                self.config.realtime_window_sec,
            ),
        )
        session_id = str(start_response["baseline_session_id"])
        amplitudes: list[np.ndarray] = []

        def capture_frame(frame: Any) -> None:
            try:
                amplitudes.append(parse_raw_csi_packet(frame.to_dict()))
            except ValueError as exc:
                LOGGER.warning(
                    "baseline packet skipped",
                    extra={
                        "event": "baseline_packet_skipped",
                        "frame_id": frame.id,
                        "error": str(exc),
                    },
                )

        capture_runtime = self.capture_runtime or SetupCaptureRuntime(
            self.config
        )
        summary = capture_runtime.run(
            duration_sec=duration_sec,
            on_frame=capture_frame,
        )
        return self.finish_collection(
            session_id=session_id,
            duration_sec=duration_sec,
            amplitudes=amplitudes,
            parse_error_count=summary.parse_error_count,
            ignored_line_count=summary.ignored_line_count,
        )

    def finish_collection(
        self,
        *,
        session_id: str,
        duration_sec: float,
        amplitudes: list[np.ndarray],
        parse_error_count: int = 0,
        ignored_line_count: int = 0,
    ) -> SetupState:
        """Build, persist, and register a baseline from captured amplitudes."""

        session_dir = self._session_dir("baseline", session_id)
        if len(amplitudes) < 128:
            raise BaselineCollectionError(
                f"baseline needs at least 128 valid packets; got {len(amplitudes)}"
            )
        matrix = np.vstack(amplitudes).astype(np.float32)
        state = build_baseline_state(
            matrix,
            baseline_id=self.config.baseline_id,
            duration_sec=duration_sec,
            window_duration_sec=self.config.realtime_window_sec,
            preprocessing_type=NORMALIZATION_VERSION,
        )
        candidate_baseline_path = save_baseline_state(
            session_dir / "baseline_state.json",
            state,
        )
        score_summary = baseline_score_summary(matrix, state)
        score_summary.update(
            {
                "parse_error_count": float(parse_error_count),
                "ignored_line_count": float(ignored_line_count),
            }
        )
        payload = build_baseline_finish_payload(
            session_id,
            state,
            score_summary,
        )
        envelope = build_finish_envelope(
            operation="baseline_finish",
            room_id=self.config.room_id,
            server_url=self.config.model_server_url,
            payload=payload,
            candidate_baseline_state_path=str(
                candidate_baseline_path.resolve()
            ),
        )
        payload_path = session_dir / "baseline_finish_payload.json"
        atomic_write_json(payload_path, envelope)
        self._save_amplitude_if_enabled(
            matrix,
            session_dir,
            self.config.baseline_id,
        )
        try:
            finish_response = self.api_client.baseline_finish(
                self.config.room_id,
                payload,
            )
        except SetupApiError as exc:
            self.spool_store.create(
                envelope=envelope,
                source_payload_path=payload_path,
                last_error=str(exc),
            )
            raise BaselineCollectionError(str(exc)) from exc
        try:
            baseline_path = save_baseline_state(
                self.config.baseline_state_json,
                state,
            )
        except Exception as exc:
            self.spool_store.create(
                envelope=envelope,
                source_payload_path=payload_path,
                last_error=str(exc),
            )
            raise BaselineCollectionError(
                f"baseline registered but local activation failed: {exc}"
            ) from exc
        server_status = finish_response
        if not finish_response.get("calibration_id"):
            try:
                server_status = self.api_client.setup_status(
                    self.config.room_id
                )
            except SetupApiError:
                # Older servers return baseline_ready without an automatic
                # calibration profile. Preserve that compatible local state.
                server_status = finish_response
        calibration_id = server_status.get("calibration_id")
        setup_status = (
            "ready"
            if calibration_id
            and server_status.get("status") in {"ready", "running"}
            else "baseline_ready"
        )
        now = utc_now()
        existing = self.state_store.load(self.config.room_id)
        setup = SetupState(
            room_id=self.config.room_id,
            device_group_id=self.config.device_group_id,
            baseline_id=self.config.baseline_id,
            calibration_id=(
                str(calibration_id) if calibration_id else None
            ),
            baseline_state_json=str(baseline_path),
            server_url=self.config.model_server_url,
            feature_version=STAGE1_FEATURE_VERSION,
            preprocessing_type=NORMALIZATION_VERSION,
            setup_status=setup_status,
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )
        self.state_store.save(setup)
        return setup

    def _session_dir(self, prefix: str, session_id: str) -> Path:
        path = (
            self.config.local_setup_dir
            / self.config.room_id
            / f"{prefix}_{session_id}"
        )
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _save_amplitude_if_enabled(
        self,
        matrix: np.ndarray,
        session_dir: Path,
        identifier: str,
    ) -> None:
        if not self.config.amplitude_npy_enabled:
            return
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = session_dir / (
            f"{self.config.room_id}_{identifier}_{timestamp}.npy"
        )
        try:
            np.save(path, matrix, allow_pickle=False)
        except Exception as exc:
            LOGGER.warning(
                "baseline amplitude save failed",
                extra={
                    "event": "amplitude_npy_save_failed",
                    "path": str(path),
                    "error": str(exc),
                },
            )
