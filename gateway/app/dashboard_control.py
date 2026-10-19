"""Poll model-server setup commands without owning the UART connection."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

import numpy as np

from app.baseline_collector import BaselineCollector
from app.config import GatewayConfig
from app.csi_preprocessor import parse_raw_csi_packet
from app.setup_api_client import SetupApiClient, SetupApiError
from app.setup_spool import SetupSpoolStore
from app.setup_state import SetupState, SetupStateStore


LOGGER = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


class DashboardBaselineController:
    """Turn a persisted server command into an in-process baseline capture."""

    def __init__(
        self,
        config: GatewayConfig,
        *,
        api_client: SetupApiClient | None = None,
        state_store: SetupStateStore | None = None,
        spool_store: SetupSpoolStore | None = None,
        clock: Callable[[], float] = time.monotonic,
        status_callback: Callable[..., None] | None = None,
    ) -> None:
        self.config = config
        self.api_client = api_client or SetupApiClient(
            config.model_server_url,
            config.model_server_timeout_sec,
        )
        self.state_store = state_store or SetupStateStore(
            config.local_setup_dir
        )
        self.spool_store = spool_store or SetupSpoolStore(
            config.local_setup_spool_dir
        )
        self.collector = BaselineCollector(
            config,
            self.api_client,
            self.state_store,
            self.spool_store,
        )
        self.clock = clock
        self.status_callback = status_callback
        self._lock = threading.Lock()
        self._next_poll_at = 0.0
        self._mode = "realtime"
        self._command: dict[str, Any] | None = None
        self._started_at = 0.0
        self._duration_sec = config.baseline_collection_sec
        self._amplitudes: list[np.ndarray] = []
        self._parse_error_count = 0
        self._last_handled_command_id: str | None = None
        self._last_duplicate_command_id: str | None = None

    @property
    def suppresses_realtime(self) -> bool:
        with self._lock:
            return self._mode in {"baseline_collecting", "baseline_finalizing"}

    @property
    def mode(self) -> str:
        with self._lock:
            return self._mode

    def poll(self, *, force: bool = False) -> None:
        if not self.config.dashboard_control_enabled:
            return
        now = self.clock()
        with self._lock:
            if not force and now < self._next_poll_at:
                return
            self._next_poll_at = now + self.config.remote_setup_poll_sec
        try:
            setup = self.api_client.setup_status(self.config.room_id)
        except SetupApiError as exc:
            LOGGER.warning(
                "dashboard control poll failed",
                extra={
                    "event": "dashboard_control_poll_failed",
                    "room_id": self.config.room_id,
                    "error": str(exc),
                },
            )
            return

        command = setup.get("baseline_command")
        if not isinstance(command, dict):
            return
        if command.get("status") not in {"pending", "collecting"}:
            return
        self._accept_command(command)

    def capture_frame(self, frame: Any) -> None:
        if not self.suppresses_realtime or self.mode != "baseline_collecting":
            return
        try:
            amplitude = parse_raw_csi_packet(frame.to_dict())
        except ValueError as exc:
            self.record_parse_error()
            LOGGER.warning(
                "baseline packet skipped",
                extra={
                    "event": "baseline_packet_skipped",
                    "frame_id": frame.id,
                    "error": str(exc),
                },
            )
            return
        with self._lock:
            if self._mode == "baseline_collecting":
                self._amplitudes.append(amplitude)

    def record_parse_error(self) -> None:
        with self._lock:
            if self._mode == "baseline_collecting":
                self._parse_error_count += 1

    def tick(self) -> SetupState | None:
        with self._lock:
            if self._mode != "baseline_collecting":
                return None
            elapsed = self.clock() - self._started_at
            self._status(
                "baseline_collection_progress",
                elapsed_sec=elapsed,
                duration_sec=self._duration_sec,
                packet_count=len(self._amplitudes),
            )
            if elapsed < self._duration_sec:
                return None
            self._mode = "baseline_finalizing"
            command = dict(self._command or {})
            amplitudes = self._amplitudes
            parse_error_count = self._parse_error_count
            self._amplitudes = []

        command_id = str(command.get("command_id", ""))
        session_id = str(command.get("baseline_session_id", command_id))
        try:
            setup = self.collector.finish_collection(
                session_id=session_id,
                duration_sec=self._duration_sec,
                amplitudes=amplitudes,
                parse_error_count=parse_error_count,
            )
        except Exception as exc:
            self._finish_failed(command_id, exc)
            return None

        with self._lock:
            self._mode = "realtime"
            self._last_handled_command_id = command_id
            self._command = None
        self._status(
            "gateway_mode_changed",
            mode="realtime",
            baseline_collection_in_progress=False,
        )
        LOGGER.info(
            "dashboard baseline collection completed",
            extra={
                "event": "dashboard_baseline_completed",
                "command_type": "baseline/start",
                "room_id": self.config.room_id,
                "baseline_id": setup.baseline_id,
                "command_id": command_id,
                "packet_count": len(amplitudes),
                "accepted": True,
            },
        )
        return setup

    def abort(self, reason: str) -> None:
        """Fail an active command so a later dashboard click can retry it."""

        with self._lock:
            if self._mode not in {"baseline_collecting", "baseline_finalizing"}:
                return
            command_id = str((self._command or {}).get("command_id", ""))
        self._finish_failed(command_id, RuntimeError(reason))

    def _accept_command(self, command: dict[str, Any]) -> None:
        command_id = str(
            command.get("command_id")
            or command.get("baseline_session_id")
            or ""
        )
        if not command_id:
            return
        with self._lock:
            current_id = (
                str(self._command.get("command_id"))
                if self._command is not None
                else None
            )
            if command_id == self._last_handled_command_id:
                return
            if self._mode in {"baseline_collecting", "baseline_finalizing"}:
                if command_id != current_id and command_id != self._last_duplicate_command_id:
                    self._last_duplicate_command_id = command_id
                    self._log_command(command, False, "baseline_collection_in_progress")
                return
            duration = float(
                command.get("duration_sec")
                or self.config.baseline_collection_sec
            )
            if duration <= 0:
                duration = self.config.baseline_collection_sec
            self._command = dict(command)
            self._command["command_id"] = command_id
            self._duration_sec = duration
            self._started_at = self.clock()
            self._amplitudes = []
            self._parse_error_count = 0
            self._mode = "baseline_collecting"
            self._last_duplicate_command_id = None

        try:
            self.api_client.baseline_command_status(
                self.config.room_id,
                command_id=command_id,
                status="collecting",
                gateway_id=self.config.gateway_id,
            )
        except SetupApiError as exc:
            LOGGER.warning(
                "baseline command acknowledgement failed",
                extra={
                    "event": "baseline_command_ack_failed",
                    "command_id": command_id,
                    "room_id": self.config.room_id,
                    "error": str(exc),
                },
            )
        self._status(
            "gateway_mode_changed",
            mode="baseline_collecting",
            baseline_collection_in_progress=True,
            baseline_command_id=command_id,
            baseline_collection_duration_sec=duration,
        )
        self._log_command(command, True, None)

    def _finish_failed(self, command_id: str, exc: Exception) -> None:
        error = str(exc)
        try:
            self.api_client.baseline_command_status(
                self.config.room_id,
                command_id=command_id,
                status="failed",
                gateway_id=self.config.gateway_id,
                message=error,
            )
        except SetupApiError as status_exc:
            LOGGER.warning(
                "baseline failure status update failed",
                extra={
                    "event": "baseline_command_status_failed",
                    "command_id": command_id,
                    "error": str(status_exc),
                },
            )
        with self._lock:
            self._mode = "realtime"
            self._last_handled_command_id = command_id
            self._command = None
        self._status(
            "gateway_mode_changed",
            mode="realtime",
            baseline_collection_in_progress=False,
            baseline_collection_error=error,
        )
        LOGGER.error(
            "dashboard baseline collection failed",
            extra={
                "event": "dashboard_baseline_failed",
                "command_type": "baseline/start",
                "room_id": self.config.room_id,
                "command_id": command_id,
                "error": error,
                "accepted": True,
            },
        )

    def _log_command(
        self,
        command: dict[str, Any],
        accepted: bool,
        ignored_reason: str | None,
    ) -> None:
        LOGGER.info(
            "dashboard baseline command received",
            extra={
                "event": "dashboard_command_received",
                "command_type": "baseline/start",
                "room_id": self.config.room_id,
                "baseline_id": self.config.baseline_id,
                "baseline_session_id": command.get("baseline_session_id"),
                "requested_duration_sec": command.get(
                    "duration_sec", self.config.baseline_collection_sec
                ),
                "command_received_at": _utc_now_iso(),
                "current_gateway_mode": self.mode,
                "accepted": accepted,
                "ignored_reason": ignored_reason,
            },
        )

    def _status(self, event: str, **values: Any) -> None:
        if self.status_callback is not None:
            self.status_callback(event, **values)
