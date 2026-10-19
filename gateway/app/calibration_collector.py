"""Explicit empty-room threshold calibration workflow."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from app.baseline_state import load_baseline_state
from app.config import GatewayConfig
from app.csi_preprocessor import (
    WINDOW_SIZE,
    parse_raw_csi_packet,
    stage1_feature_vector,
)
from app.realtime_payload_builder import build_window_id
from app.setup_api_client import SetupApiClient, SetupApiError
from app.setup_capture import SetupCaptureRuntime
from app.setup_payload_builder import (
    build_calibration_finish_payload,
    build_calibration_start_payload,
    build_calibration_window_payload,
    build_finish_envelope,
)
from app.setup_spool import SetupSpoolStore
from app.setup_state import SetupState, SetupStateStore, atomic_write_json
from app.window_buffer import CompletedWindow


LOGGER = logging.getLogger(__name__)


class CalibrationCollectionError(RuntimeError):
    pass


class CalibrationCollector:
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
        self.capture_runtime = capture_runtime or SetupCaptureRuntime(config)

    def collect(self, expected_windows: int = 12) -> SetupState:
        if expected_windows <= 0:
            raise ValueError("expected_windows must be greater than zero")
        setup = self.state_store.load(self.config.room_id)
        if setup is None or not setup.baseline_id:
            raise CalibrationCollectionError(
                "baseline setup state is required before calibration"
            )
        baseline = load_baseline_state(setup.baseline_state_json)
        start_response = self.api_client.calibration_start(
            self.config.room_id,
            build_calibration_start_payload(
                setup.baseline_id,
                expected_windows,
                self.config.realtime_window_sec,
            ),
        )
        session_id = str(start_response["calibration_session_id"])
        session_dir = self._session_dir(session_id)
        accepted: list[np.ndarray] = []
        accepted_count = 0

        def process_window(window: CompletedWindow) -> bool:
            nonlocal accepted_count
            amplitudes: list[np.ndarray] = []
            for record in window.records:
                try:
                    amplitudes.append(parse_raw_csi_packet(record))
                except ValueError:
                    continue
            if len(amplitudes) < WINDOW_SIZE:
                LOGGER.info(
                    "calibration window skipped",
                    extra={
                        "event": "calibration_window_skipped",
                        "reason": "insufficient_packets",
                        "frame_count": len(amplitudes),
                        "window_start": window.window_start,
                    },
                )
                return False
            matrix = np.vstack(amplitudes[-WINDOW_SIZE:]).astype(np.float32)
            features = stage1_feature_vector(matrix, baseline)
            window_id = build_window_id(
                self.config.room_id,
                window.window_start,
            )
            payload = build_calibration_window_payload(
                session_id,
                window_id,
                setup.baseline_id or "",
                features,
            )
            payload_path = session_dir / (
                f"calibration_window_{accepted_count + 1:03d}.json"
            )
            atomic_write_json(payload_path, payload)
            self.api_client.calibration_window(
                self.config.room_id,
                payload,
            )
            accepted.append(matrix)
            accepted_count += 1
            return accepted_count >= expected_windows

        try:
            self.capture_runtime.run(on_realtime_window=process_window)
        except SetupApiError as exc:
            raise CalibrationCollectionError(str(exc)) from exc
        if accepted_count < expected_windows:
            raise CalibrationCollectionError(
                f"calibration stopped after {accepted_count} accepted windows"
            )
        finish_payload = build_calibration_finish_payload(session_id)
        envelope = build_finish_envelope(
            operation="calibration_finish",
            room_id=self.config.room_id,
            server_url=self.config.model_server_url,
            payload=finish_payload,
        )
        payload_path = session_dir / "calibration_finish_payload.json"
        atomic_write_json(payload_path, envelope)
        try:
            response = self.api_client.calibration_finish(
                self.config.room_id,
                finish_payload,
            )
        except SetupApiError as exc:
            self.spool_store.create(
                envelope=envelope,
                source_payload_path=payload_path,
                last_error=str(exc),
            )
            raise CalibrationCollectionError(str(exc)) from exc
        calibration_id = response.get("calibration_id")
        if not calibration_id:
            status = self.api_client.setup_status(self.config.room_id)
            calibration_id = status.get("calibration_id")
        if not calibration_id:
            raise CalibrationCollectionError(
                "calibration finish response did not include calibration_id"
            )
        ready = setup.updated(
            calibration_id=str(calibration_id),
            setup_status="ready",
        )
        self.state_store.save(ready)
        self._save_amplitude_if_enabled(
            accepted,
            session_dir,
            str(calibration_id),
        )
        return ready

    def _session_dir(self, session_id: str) -> Path:
        path = (
            self.config.local_setup_dir
            / self.config.room_id
            / f"calibration_{session_id}"
        )
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _save_amplitude_if_enabled(
        self,
        windows: list[np.ndarray],
        session_dir: Path,
        calibration_id: str,
    ) -> None:
        if not self.config.amplitude_npy_enabled:
            return
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = session_dir / (
            f"{self.config.room_id}_{calibration_id}_{timestamp}.npy"
        )
        try:
            np.save(path, np.stack(windows), allow_pickle=False)
        except Exception as exc:
            LOGGER.warning(
                "calibration amplitude save failed",
                extra={
                    "event": "amplitude_npy_save_failed",
                    "path": str(path),
                    "error": str(exc),
                },
            )
