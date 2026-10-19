"""Thread-safe, non-blocking dashboard status snapshots."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from collections import deque
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


LOGGER = logging.getLogger(__name__)
STATUS_SCHEMA_VERSION = "1.0"


def _iso_utc(timestamp: float) -> str:
    return (
        datetime.fromtimestamp(timestamp, tz=timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


class GatewayStatus:
    """Keep scalar operational status without storing CSI or payload arrays."""

    def __init__(
        self,
        *,
        gateway_id: str,
        device_id: str,
        link_id: str,
        serial_port: str,
        aws_region: str,
        s3_upload_enabled: bool,
        realtime_send_enabled: bool,
        baseline_state_path: Path,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.clock = clock
        self.started_at = clock()
        self._lock = threading.Lock()
        self._frame_times: deque[float] = deque()
        self._realtime_bucket_start: float | None = None
        self._state: dict[str, Any] = {
            "gateway": {
                "gateway_id": gateway_id,
                "device_id": device_id,
                "link_id": link_id,
                "started_at": _iso_utc(self.started_at),
                "current_time": _iso_utc(self.started_at),
                "uptime_sec": 0.0,
                "serial_port": serial_port,
                "aws_region": aws_region,
                "s3_upload_enabled": s3_upload_enabled,
                "realtime_send_enabled": realtime_send_enabled,
                "mode": "realtime",
            },
            "uart": {
                "serial_connected": False,
                "last_connected_at": None,
                "last_received_at": None,
                "total_lines": 0,
                "valid_frames": 0,
                "parse_errors": 0,
                "ignored_lines": 0,
                "fps_1s": 0.0,
                "fps_5s": 0.0,
            },
            "firmware": {
                "protocol_ready": False,
                "protocol_version": None,
                "receiver_mac": None,
                "min_rate_hz": None,
                "max_rate_hz": None,
                "min_tx_power_dbm": None,
                "max_tx_power_dbm": None,
                "command_pending": False,
                "last_command": None,
                "last_request_id": None,
                "requested_rate_hz": None,
                "requested_tx_power_dbm": None,
                "actual_rate_hz": None,
                "actual_tx_power_dbm": None,
                "last_status_code": None,
                "last_ack_at": None,
                "last_error": None,
            },
            "csi_quality": {
                "latest_rssi": None,
                "latest_noise_floor": None,
                "rssi_min_5s": None,
                "rssi_max_5s": None,
                "rssi_avg_5s": None,
                "noise_floor_avg_5s": None,
            },
            "windows": {
                "current_realtime_frame_count": 0,
                "last_window_id": None,
                "last_window_frame_count": None,
                "minimum_packets_met": None,
                "last_skipped": None,
                "last_skip_reason": None,
                "motion_input_created": False,
                "motion_input_shape": None,
            },
            "baseline": {
                "path": str(baseline_state_path),
                "setup_status": "unavailable",
                "loaded": False,
                "baseline_id": None,
                "calibration_id": None,
                "valid": False,
                "last_loaded_at": None,
                "last_error": None,
                "collection_in_progress": False,
                "collection_command_id": None,
                "collection_duration_sec": None,
                "collection_elapsed_sec": None,
                "collection_packet_count": 0,
            },
            "scores": {
                "static_score": None,
                "motion_score": None,
                "shape_score": None,
                "robust_max_shape_score": None,
            },
            "realtime": {
                "send_enabled": realtime_send_enabled,
                "last_payload_created_at": None,
                "last_payload_path": None,
                "last_http_success": None,
                "last_http_status_code": None,
                "last_http_error": None,
                "pending_spool_count": 0,
            },
            "s3": {
                "rawlog_enabled": True,
                "upload_enabled": s3_upload_enabled,
                "last_rawlog_path": None,
                "last_upload_success": None,
                "last_error": None,
                "pending_spool_count": 0,
            },
            "files": {
                "latest_rawlog": None,
                "latest_preprocessed": None,
                "latest_dryrun_payload": None,
            },
        }

    def record_event(self, event: str, **values: Any) -> None:
        """Apply one small status event; never raise into the caller."""

        try:
            with self._lock:
                self._record_event_locked(event, values)
        except Exception as exc:
            LOGGER.warning(
                "dashboard status update failed",
                extra={
                    "event": "dashboard_status_update_failed",
                    "status_event": event,
                    "error": str(exc),
                },
            )

    def snapshot(self) -> dict[str, Any]:
        now = self.clock()
        with self._lock:
            current_bucket = int(now // 5) * 5.0
            if self._realtime_bucket_start != current_bucket:
                self._state["windows"]["current_realtime_frame_count"] = 0
            self._update_rates_locked(now)
            state = deepcopy(self._state)
        state["gateway"]["current_time"] = _iso_utc(now)
        state["gateway"]["uptime_sec"] = round(
            max(0.0, now - self.started_at), 3
        )
        return {
            "schema_version": STATUS_SCHEMA_VERSION,
            "updated_at": _iso_utc(now),
            **state,
        }

    def write_snapshot(self, path: Path) -> bool:
        """Atomically write one snapshot and isolate all filesystem errors."""

        destination = Path(path)
        temporary_path: Path | None = None
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
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
                    self.snapshot(),
                    temporary,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                temporary.write("\n")
                temporary.flush()
                os.fsync(temporary.fileno())
            temporary_path.replace(destination)
            return True
        except Exception as exc:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            LOGGER.warning(
                "dashboard status write failed",
                extra={
                    "event": "dashboard_status_write_failed",
                    "status_path": str(destination),
                    "error": str(exc),
                },
            )
            return False

    def _record_event_locked(
        self,
        event: str,
        values: dict[str, Any],
    ) -> None:
        timestamp = float(values.get("timestamp", self.clock()))
        if event == "serial_connected":
            self._state["uart"]["serial_connected"] = True
            self._state["uart"]["last_connected_at"] = _iso_utc(timestamp)
        elif event == "serial_disconnected":
            self._state["uart"]["serial_connected"] = False
            self._state["firmware"]["protocol_ready"] = False
            self._state["firmware"]["command_pending"] = False
        elif event == "uart_line_received":
            self._state["uart"]["total_lines"] += 1
            self._state["uart"]["last_received_at"] = _iso_utc(timestamp)
        elif event == "frame_parsed":
            self._state["uart"]["valid_frames"] += 1
            self._frame_times.append(timestamp)
            bucket_start = int(timestamp // 5) * 5.0
            if self._realtime_bucket_start != bucket_start:
                self._realtime_bucket_start = bucket_start
                self._state["windows"]["current_realtime_frame_count"] = 0
            self._state["windows"]["current_realtime_frame_count"] += 1
            self._state["csi_quality"]["latest_rssi"] = values.get("rssi")
            self._state["csi_quality"]["latest_noise_floor"] = values.get(
                "noise_floor"
            )
            self._update_rates_locked(timestamp)
        elif event == "parse_error":
            self._state["uart"]["parse_errors"] += 1
        elif event == "line_ignored":
            self._state["uart"]["ignored_lines"] += 1
        elif event == "firmware_ready":
            firmware = self._state["firmware"]
            firmware["protocol_ready"] = True
            firmware["protocol_version"] = values.get("protocol_version")
            firmware["receiver_mac"] = values.get("receiver_mac")
            firmware["min_rate_hz"] = values.get("min_rate_hz")
            firmware["max_rate_hz"] = values.get("max_rate_hz")
            firmware["min_tx_power_dbm"] = self._dbm(
                values.get("min_tx_power_qdbm")
            )
            firmware["max_tx_power_dbm"] = self._dbm(
                values.get("max_tx_power_qdbm")
            )
            firmware["last_error"] = None
        elif event == "firmware_command_requested":
            firmware = self._state["firmware"]
            firmware["command_pending"] = True
            firmware["last_command"] = values.get("command")
            firmware["last_request_id"] = values.get("request_id")
            firmware["requested_rate_hz"] = values.get("rate_hz")
            firmware["requested_tx_power_dbm"] = self._dbm(
                values.get("tx_power_qdbm")
            )
            firmware["last_status_code"] = None
            firmware["last_error"] = None
        elif event == "firmware_command_sent":
            firmware = self._state["firmware"]
            firmware["last_request_id"] = values.get("request_id")
            firmware["last_status_code"] = values.get("status_code")
            if values.get("status_code") != 0:
                firmware["command_pending"] = False
                firmware["last_error"] = values.get("error")
        elif event == "firmware_command_ack":
            firmware = self._state["firmware"]
            firmware["command_pending"] = False
            firmware["last_request_id"] = values.get("request_id")
            firmware["actual_rate_hz"] = values.get("rate_hz")
            firmware["actual_tx_power_dbm"] = self._dbm(
                values.get("tx_power_qdbm")
            )
            firmware["last_status_code"] = values.get("status_code")
            firmware["last_ack_at"] = _iso_utc(timestamp)
            firmware["last_error"] = values.get("error")
        elif event in {"firmware_command_failed", "firmware_protocol_error"}:
            firmware = self._state["firmware"]
            firmware["command_pending"] = False
            if values.get("request_id") is not None:
                firmware["last_request_id"] = values.get("request_id")
            firmware["last_error"] = values.get("error")
        elif event == "gateway_mode_changed":
            self._state["gateway"]["mode"] = values.get("mode", "realtime")
            baseline = self._state["baseline"]
            baseline["collection_in_progress"] = bool(
                values.get("baseline_collection_in_progress", False)
            )
            baseline["collection_command_id"] = values.get(
                "baseline_command_id"
            )
            baseline["collection_duration_sec"] = values.get(
                "baseline_collection_duration_sec"
            )
            baseline["last_error"] = values.get(
                "baseline_collection_error"
            )
            if not baseline["collection_in_progress"]:
                baseline["collection_elapsed_sec"] = None
                baseline["collection_packet_count"] = 0
        elif event == "baseline_collection_progress":
            baseline = self._state["baseline"]
            baseline["collection_in_progress"] = True
            baseline["collection_elapsed_sec"] = round(
                float(values.get("elapsed_sec", 0.0)), 3
            )
            baseline["collection_duration_sec"] = values.get(
                "duration_sec"
            )
            baseline["collection_packet_count"] = int(
                values.get("packet_count", 0)
            )
        elif event == "realtime_window_completed":
            windows = self._state["windows"]
            quality = self._state["csi_quality"]
            windows["last_window_id"] = values.get("window_id")
            windows["last_window_frame_count"] = values.get("frame_count")
            windows["minimum_packets_met"] = values.get(
                "minimum_packets_met"
            )
            windows["last_skipped"] = False
            windows["last_skip_reason"] = None
            windows["motion_input_created"] = False
            windows["motion_input_shape"] = None
            quality["rssi_min_5s"] = values.get("rssi_min")
            quality["rssi_max_5s"] = values.get("rssi_max")
            quality["rssi_avg_5s"] = values.get("rssi_avg")
            quality["noise_floor_avg_5s"] = values.get("noise_floor_avg")
        elif event == "realtime_window_skipped":
            windows = self._state["windows"]
            reason = values.get("reason")
            windows["last_skipped"] = True
            windows["last_skip_reason"] = reason
            if reason in {
                "insufficient_packets",
                "motion_input_generation_failed",
            }:
                windows["motion_input_created"] = False
                windows["motion_input_shape"] = None
            windows["minimum_packets_met"] = (
                reason != "insufficient_packets"
            )
        elif event == "motion_input_created":
            self._state["windows"]["motion_input_created"] = True
            self._state["windows"]["motion_input_shape"] = values.get("shape")
            self._state["windows"]["last_window_id"] = values.get("window_id")
        elif event == "baseline_state_loaded":
            baseline = self._state["baseline"]
            baseline["loaded"] = True
            baseline["valid"] = True
            baseline["baseline_id"] = values.get("baseline_id")
            baseline["last_loaded_at"] = _iso_utc(timestamp)
            baseline["last_error"] = None
        elif event == "setup_state_changed":
            baseline = self._state["baseline"]
            baseline["setup_status"] = values.get("setup_status")
            baseline["baseline_id"] = values.get("baseline_id")
            baseline["calibration_id"] = values.get("calibration_id")
            if values.get("baseline_state_path"):
                baseline["path"] = values["baseline_state_path"]
        elif event in {"baseline_state_unavailable", "baseline_state_invalid"}:
            baseline = self._state["baseline"]
            baseline["loaded"] = False
            baseline["valid"] = False
            baseline["last_error"] = values.get("error")
        elif event == "scores_updated":
            for key in self._state["scores"]:
                self._state["scores"][key] = values.get(key)
        elif event == "realtime_payload_created":
            self._state["realtime"]["last_payload_created_at"] = _iso_utc(
                timestamp
            )
        elif event == "realtime_payload_dry_run":
            path = values.get("local_payload_path")
            self._state["realtime"]["last_payload_path"] = path
            self._state["files"]["latest_dryrun_payload"] = path
        elif event == "realtime_payload_sent":
            realtime = self._state["realtime"]
            realtime["last_http_success"] = True
            realtime["last_http_status_code"] = values.get("status_code")
            realtime["last_http_error"] = None
        elif event == "realtime_payload_send_failed":
            realtime = self._state["realtime"]
            realtime["last_http_success"] = False
            realtime["last_http_status_code"] = values.get("status_code")
            realtime["last_http_error"] = values.get("error")
        elif event == "rawlog_written":
            path = values.get("path")
            self._state["s3"]["last_rawlog_path"] = path
            self._state["files"]["latest_rawlog"] = path
        elif event == "preprocessed_written":
            path = values.get("path")
            self._state["files"]["latest_preprocessed"] = path
            self._state["realtime"]["last_payload_path"] = path
        elif event == "s3_upload_result":
            if values.get("payload_type") == "rawlog_1m":
                self._state["s3"]["last_upload_success"] = values.get(
                    "success"
                )
                self._state["s3"]["last_error"] = values.get("error")
        elif event == "spool_counts":
            self._state["s3"]["pending_spool_count"] = int(
                values.get("s3_pending", 0)
            )
            self._state["realtime"]["pending_spool_count"] = int(
                values.get("realtime_pending", 0)
            )

    @staticmethod
    def _dbm(qdbm: Any) -> float | None:
        if qdbm is None:
            return None
        return float(qdbm) / 4.0

    def _update_rates_locked(self, now: float) -> None:
        while self._frame_times and self._frame_times[0] < now - 5.0:
            self._frame_times.popleft()
        fps_1s = sum(
            1 for timestamp in self._frame_times if timestamp >= now - 1.0
        )
        self._state["uart"]["fps_1s"] = float(fps_1s)
        self._state["uart"]["fps_5s"] = round(
            len(self._frame_times) / 5.0, 3
        )


class StatusSnapshotService:
    """Write snapshots on a daemon thread at a fixed interval."""

    def __init__(
        self,
        status: GatewayStatus,
        path: Path,
        update_sec: float,
    ) -> None:
        if update_sec <= 0:
            raise ValueError("update_sec must be greater than zero")
        self.status = status
        self.path = Path(path)
        self.update_sec = update_sec
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run,
            name="dashboard-status-writer",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self.update_sec + 1.0)
        self.status.write_snapshot(self.path)

    def _run(self) -> None:
        self.status.write_snapshot(self.path)
        while not self._stop_event.wait(self.update_sec):
            self.status.write_snapshot(self.path)
