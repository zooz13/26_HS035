"""ESP32 Wi-Fi CSI UART Gateway entry point."""

from __future__ import annotations

import argparse
import logging
import signal
import threading
import time
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

import numpy as np

from app.baseline_state import BaselineStateManager
from app.baseline_collector import BaselineCollectionError, BaselineCollector
from app.calibration_collector import (
    CalibrationCollectionError,
    CalibrationCollector,
)
from app.config import ConfigurationError, GatewayConfig
from app.csi_parser import CsiParser, ParseStatus
from app.csi_preprocessor import (
    WINDOW_SIZE,
    build_motion_input,
    parse_raw_csi_packet,
    stage1_feature_vector,
)
from app.dashboard_control import DashboardBaselineController
from app.dashboard_server import DashboardHttpServer
from app.firmware_control import FirmwareController
from app.jsonl_writer import WindowFileWriter
from app.logging_config import configure_logging
from app.model_server_client import ModelServerClient
from app.models import PayloadType, epoch_utc_isoformat
from app.preprocessed_log import PreprocessedLogWriter
from app.realtime_payload_builder import (
    build_realtime_window_payload,
    build_window_id,
)
from app.realtime_spool import RealtimeSpoolStore
from app.s3_uploader import S3Uploader
from app.spool import SpoolStore
from app.setup_api_client import SetupApiClient, SetupApiError
from app.setup_payload_builder import build_room_create_payload
from app.setup_spool import SetupSpoolStore
from app.setup_state import (
    SetupState,
    SetupStateManager,
    SetupStateStore,
    utc_now,
)
from app.transport_test import (
    bootstrap_transport_test,
    transport_test_features,
)
from app.status_snapshot import GatewayStatus, StatusSnapshotService
from app.uart_reader import UartReader
from app.window_buffer import CompletedWindow, WindowBuffer


LOGGER = logging.getLogger(__name__)


def config_summary(config: GatewayConfig) -> dict[str, object]:
    """Return the allowlisted, non-secret startup configuration summary."""

    return {
        "gateway_id": config.gateway_id,
        "device_id": config.device_id,
        "link_id": config.link_id,
        "serial_port": config.serial_port,
        "serial_baudrate": config.serial_baudrate,
        "s3_upload_enabled": config.s3_upload_enabled,
        "realtime_send_enabled": config.realtime_send_enabled,
        "transport_test_enabled": config.transport_test_enabled,
        "dashboard_control_enabled": config.dashboard_control_enabled,
        "aws_region": config.aws_region,
        "s3_bucket_configured": bool(config.s3_bucket),
    }


def check_config(config: GatewayConfig) -> int:
    """Validate local configuration and baseline without external I/O."""

    LOGGER.info(
        "gateway configuration",
        extra={
            "event": "config_summary",
            **config_summary(config),
        },
    )
    baseline = None
    if not config.transport_test_enabled:
        baseline = BaselineStateManager(
            config.baseline_state_json,
            config.baseline_id,
            reload_sec=config.baseline_reload_sec,
        ).refresh(force=True)
    LOGGER.info(
        "configuration check completed",
        extra={
            "event": "config_check_completed",
            "baseline_available": baseline is not None,
            "baseline_state_path": str(config.baseline_state_json),
            "transport_test_enabled": config.transport_test_enabled,
        },
    )
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="ESP32 Wi-Fi CSI UART Gateway"
    )
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="validate configuration and baseline without external connections",
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=[
            "room-create",
            "baseline-collect",
            "calibration-collect",
            "transport-test-bootstrap",
            "realtime",
        ],
        default="realtime",
    )
    parser.add_argument(
        "--duration-sec",
        type=float,
        default=300.0,
        help="empty-room baseline collection duration",
    )
    parser.add_argument(
        "--expected-windows",
        type=int,
        default=12,
        help="number of accepted empty calibration windows",
    )
    return parser


class GatewayApplication:
    def __init__(self, config: GatewayConfig) -> None:
        self.config = config
        self.stop_event = threading.Event()
        self.buffer_lock = threading.Lock()
        self.status = (
            GatewayStatus(
                gateway_id=config.gateway_id,
                device_id=config.device_id,
                link_id=config.link_id,
                serial_port=config.serial_port,
                aws_region=config.aws_region,
                s3_upload_enabled=config.s3_upload_enabled,
                realtime_send_enabled=config.realtime_send_enabled,
                baseline_state_path=config.baseline_state_json,
            )
            if config.dashboard_status_enabled or config.dashboard_http_enabled
            else None
        )
        self.status_service = (
            StatusSnapshotService(
                self.status,
                config.dashboard_status_path,
                config.dashboard_update_sec,
            )
            if self.status is not None
            else None
        )
        status_callback = (
            self.status.record_event if self.status is not None else None
        )
        self.setup_state_store = SetupStateStore(config.local_setup_dir)
        self.setup_state_manager = SetupStateManager(
            self.setup_state_store,
            config.room_id,
            reload_sec=config.setup_reload_sec,
        )
        self.active_setup: SetupState | None = None
        self.parser = CsiParser(
            config.gateway_id,
            config.device_id,
            config.link_id,
        )
        self.window_buffer = WindowBuffer(
            realtime_window_sec=config.realtime_window_sec,
            raw_log_window_sec=config.raw_log_window_sec,
        )
        self.rawlog_writer = WindowFileWriter(
            gateway_id=config.gateway_id,
            device_id=config.device_id,
            link_id=config.link_id,
            s3_bucket=config.s3_bucket,
            local_rawlog_dir=config.local_rawlog_dir,
            s3_rawlog_prefix=config.s3_rawlog_prefix,
            gzip_compress_level=config.gzip_compress_level,
            delete_uncompressed_after_compress=(
                config.delete_uncompressed_after_compress
            ),
        )
        self.s3_uploader = S3Uploader(
            s3_upload_enabled=config.s3_upload_enabled,
            delete_local_after_upload=config.delete_local_after_upload,
            spool_store=SpoolStore(config.local_spool_dir),
            model_server_url=config.model_server_url,
            timeout_sec=config.model_server_timeout_sec,
            status_callback=status_callback,
        )
        self.baseline_manager: BaselineStateManager | None = None
        self._baseline_status_callback = status_callback
        self.realtime_client = ModelServerClient(
            model_server_url=config.model_server_url,
            room_id=config.room_id,
            timeout_sec=config.model_server_timeout_sec,
            send_enabled=config.realtime_send_enabled,
            dryrun_dir=config.local_realtime_dryrun_dir,
            spool_store=RealtimeSpoolStore(
                config.local_realtime_spool_dir
            ),
            status_callback=status_callback,
        )
        self.preprocessed_writer = PreprocessedLogWriter(
            enabled=config.preprocessed_log_enabled,
            local_dir=config.local_preprocessed_log_dir,
            s3_bucket=config.s3_bucket,
            s3_prefix=config.s3_preprocessed_prefix,
            gzip_compress_level=config.gzip_compress_level,
        )
        self.uart_reader = UartReader(
            gateway_id=config.gateway_id,
            device_id=config.device_id,
            link_id=config.link_id,
            port=config.serial_port,
            baudrate=config.serial_baudrate,
            timeout_sec=config.serial_timeout_sec,
            reconnect_sec=config.serial_reconnect_sec,
            max_line_bytes=config.serial_max_line_bytes,
            error_capture_bytes=config.serial_error_capture_bytes,
            status_callback=status_callback,
        )
        self.firmware_control = FirmwareController(
            self.uart_reader.write_line,
            status_callback=status_callback,
        )
        self.dashboard_http = (
            DashboardHttpServer(
                host=config.dashboard_http_host,
                port=config.dashboard_http_port,
                dashboard_dir=config.dashboard_dir,
                firmware=self.firmware_control,
                status_snapshot=(
                    self.status.snapshot if self.status is not None else None
                ),
            )
            if config.dashboard_http_enabled
            else None
        )
        self.dashboard_control = DashboardBaselineController(
            config,
            state_store=self.setup_state_store,
            spool_store=SetupSpoolStore(config.local_setup_spool_dir),
            status_callback=status_callback,
        )
        self._maintenance_thread: threading.Thread | None = None

    def run(self) -> int:
        self._install_signal_handlers()
        if self.status_service is not None:
            self.status_service.start()
        if self.dashboard_http is not None:
            self.dashboard_http.start()
        LOGGER.info(
            "gateway configuration",
            extra={
                "event": "config_summary",
                **config_summary(self.config),
            },
        )
        self._refresh_setup_context(force=True)
        self.s3_uploader.retry_pending()
        self.realtime_client.retry_pending()
        self.dashboard_control.poll(force=True)
        self._update_spool_counts()
        self._maintenance_thread = threading.Thread(
            target=self._maintenance_loop,
            name="gateway-maintenance",
            daemon=True,
        )
        self._maintenance_thread.start()
        LOGGER.info(
            "gateway started",
            extra={
                "event": "gateway_started",
                "gateway_id": self.config.gateway_id,
                "device_id": self.config.device_id,
                "room_id": self.config.room_id,
                "transport_test_enabled": (
                    self.config.transport_test_enabled
                ),
            },
        )
        if self.config.transport_test_enabled:
            LOGGER.warning(
                "transport test mode enabled",
                extra={
                    "event": "transport_test_enabled",
                    "stage1_score": (
                        self.config.transport_test_stage1_score
                    ),
                    "note": (
                        "motion_input is real CSI preprocessing; "
                        "Stage-1 scores are fixed staging values"
                    ),
                },
            )
        parsed_server_url = urlparse(self.config.model_server_url)
        if (
            self.config.dashboard_control_enabled
            and parsed_server_url.scheme == "http"
            and parsed_server_url.hostname not in {"127.0.0.1", "localhost", "::1"}
        ):
            LOGGER.warning(
                "public development HTTP control endpoint enabled",
                extra={
                    "event": "development_public_http_warning",
                    "model_server_url": self.config.model_server_url,
                    "warning": (
                        "Port 8000 is assumed publicly accessible for development. "
                        "Restrict the EC2 Security Group before demo or operation."
                    ),
                },
            )
        if (
            self.config.dashboard_http_enabled
            and self.config.dashboard_http_host
            not in {"127.0.0.1", "localhost", "::1"}
        ):
            LOGGER.warning(
                "unauthenticated dashboard control is exposed beyond localhost",
                extra={
                    "event": "dashboard_http_non_loopback",
                    "host": self.config.dashboard_http_host,
                    "port": self.config.dashboard_http_port,
                },
            )

        try:
            for event in self.uart_reader.read_events(self.stop_event):
                if self.stop_event.is_set():
                    break
                if event.error_record is not None:
                    self._status_event("parse_error")
                    self.dashboard_control.record_parse_error()
                    with self.buffer_lock:
                        completed = self.window_buffer.add_parse_error(
                            event.error_record
                        )
                    self._process_windows(completed)
                    continue
                if event.line is None:
                    continue
                if self.firmware_control.handle_uart_line(event.line):
                    continue
                result = self.parser.parse_line(
                    event.line,
                    gateway_ts=event.gateway_ts,
                )
                if result.status is ParseStatus.PARSED and result.frame:
                    self._status_event(
                        "frame_parsed",
                        timestamp=result.frame.gateway_ts,
                        rssi=result.frame.rssi,
                        noise_floor=result.frame.noise_floor,
                    )
                    self.dashboard_control.capture_frame(result.frame)
                    with self.buffer_lock:
                        completed = self.window_buffer.add_frame(result.frame)
                    self._process_windows(completed)
                elif (
                    result.status is ParseStatus.REJECTED
                    and result.error_record is not None
                ):
                    self._status_event("parse_error")
                    self.dashboard_control.record_parse_error()
                    with self.buffer_lock:
                        completed = self.window_buffer.add_parse_error(
                            result.error_record
                        )
                    self._process_windows(completed)
                elif result.status is ParseStatus.IGNORED:
                    self._status_event("line_ignored")
        except KeyboardInterrupt:
            self.stop_event.set()
        finally:
            self.shutdown()
        return 0

    def shutdown(self) -> None:
        self.stop_event.set()
        self.uart_reader.close()
        if self.dashboard_http is not None:
            self.dashboard_http.stop()
        self.dashboard_control.abort("gateway shutdown during baseline collection")
        if (
            self._maintenance_thread is not None
            and self._maintenance_thread is not threading.current_thread()
        ):
            self._maintenance_thread.join(
                timeout=self.config.serial_timeout_sec
                + self.config.model_server_timeout_sec
                + 1,
            )
        with self.buffer_lock:
            completed = self.window_buffer.flush()
        self._process_windows(completed)
        self.s3_uploader.retry_pending()
        self.realtime_client.retry_pending()
        self._update_spool_counts()
        if self.status_service is not None:
            self.status_service.stop()
        LOGGER.info(
            "gateway stopped",
            extra={"event": "gateway_stopped"},
        )

    def _maintenance_loop(self) -> None:
        retry_at = time.monotonic()
        while not self.stop_event.wait(1.0):
            self.dashboard_control.poll()
            completed_setup = self.dashboard_control.tick()
            if completed_setup is not None:
                self._refresh_setup_context(force=True)
            self._refresh_setup_context()
            with self.buffer_lock:
                completed = self.window_buffer.advance_time(time.time())
            self._process_windows(completed)
            now = time.monotonic()
            if now >= retry_at:
                self.realtime_client.retry_pending()
                self.s3_uploader.retry_pending()
                self._update_spool_counts()
                retry_at = now + self.config.realtime_retry_sec

    def _process_windows(
        self,
        windows: Iterable[CompletedWindow],
    ) -> None:
        for window in windows:
            try:
                if window.payload_type is PayloadType.RAWLOG_1M:
                    written = self.rawlog_writer.write_window(window)
                    self._status_event(
                        "rawlog_written",
                        path=str(written.data_path),
                    )
                    self.s3_uploader.upload(written)
                    self._update_spool_counts()
                elif window.payload_type is PayloadType.REALTIME_WINDOW:
                    self._process_realtime_window(window)
            except Exception:
                LOGGER.exception(
                    "window processing failed",
                    extra={
                        "event": "window_processing_failed",
                        "payload_type": window.payload_type.value,
                        "window_start": window.window_start,
                        "window_end": window.window_end,
                    },
                )

    def _process_realtime_window(
        self,
        window: CompletedWindow,
    ) -> None:
        window_id = build_window_id(
            self.config.room_id,
            window.window_start,
        )
        self._status_event(
            "realtime_window_completed",
            window_id=window_id,
            frame_count=window.frame_count,
            minimum_packets_met=window.frame_count >= WINDOW_SIZE,
            rssi_min=window.rssi_min,
            rssi_max=window.rssi_max,
            rssi_avg=window.rssi_avg,
            noise_floor_avg=window.noise_floor_avg,
        )
        if self.dashboard_control.suppresses_realtime:
            self._status_event(
                "realtime_window_skipped",
                reason="baseline_collection_in_progress",
            )
            LOGGER.info(
                "realtime window skipped during baseline collection",
                extra={
                    "event": "realtime_window_skipped",
                    "reason": "baseline_collection_in_progress",
                    "gateway_mode": self.dashboard_control.mode,
                    "window_id": window_id,
                    "frame_count": window.frame_count,
                },
            )
            return
        amplitudes: list[np.ndarray] = []
        for record in window.records:
            try:
                amplitudes.append(parse_raw_csi_packet(record))
            except ValueError as exc:
                LOGGER.warning(
                    "realtime packet preprocessing skipped",
                    extra={
                        "event": "realtime_packet_skipped",
                        "window_start": window.window_start,
                        "packet_id": record.get("id"),
                        "error": str(exc),
                    },
                )

        if len(amplitudes) < WINDOW_SIZE:
            self._status_event(
                "realtime_window_skipped",
                reason="insufficient_packets",
            )
            LOGGER.info(
                "realtime window skipped",
                extra={
                    "event": "realtime_window_skipped",
                    "reason": "insufficient_packets",
                    "frame_count": len(amplitudes),
                    "required_frame_count": WINDOW_SIZE,
                    "window_start": window.window_start,
                    "window_end": window.window_end,
                },
            )
            return

        amplitude = np.vstack(amplitudes[-WINDOW_SIZE:]).astype(np.float32)
        try:
            motion_input = build_motion_input(amplitude)
            self._status_event(
                "motion_input_created",
                window_id=window_id,
                shape=motion_input["shape"],
            )
        except Exception as exc:
            self._status_event(
                "realtime_window_skipped",
                reason="motion_input_generation_failed",
            )
            LOGGER.error(
                "realtime window skipped",
                extra={
                    "event": "realtime_window_skipped",
                    "reason": "motion_input_generation_failed",
                    "window_start": window.window_start,
                    "window_end": window.window_end,
                    "error": str(exc),
                },
            )
            return

        setup = self._refresh_setup_context()
        if (
            setup is None
            or setup.setup_status != "ready"
            or not setup.baseline_id
            or not setup.calibration_id
        ):
            self._status_event(
                "realtime_window_skipped",
                reason="setup_state_unavailable_or_not_ready",
            )
            return
        if getattr(self.config, "transport_test_enabled", False):
            features = transport_test_features(
                self.config.transport_test_stage1_score
            )
        elif self.baseline_manager is None:
            self._status_event(
                "realtime_window_skipped",
                reason="baseline_state_unavailable_or_invalid",
            )
            return
        else:
            baseline_state = self.baseline_manager.refresh()
            if baseline_state is None:
                self._status_event(
                    "realtime_window_skipped",
                    reason="baseline_state_unavailable_or_invalid",
                )
                return
            features = stage1_feature_vector(amplitude, baseline_state)

        try:
            payload = build_realtime_window_payload(
                window_id=window_id,
                baseline_id=setup.baseline_id,
                calibration_id=setup.calibration_id,
                device_group_id=setup.device_group_id,
                window_start_time=epoch_utc_isoformat(
                    window.window_start
                ),
                window_end_time=epoch_utc_isoformat(window.window_end),
                window_duration_sec=window.window_sec,
                features=features,
                motion_input=motion_input,
            )
        except Exception as exc:
            self._status_event(
                "realtime_window_skipped",
                reason="feature_generation_failed",
            )
            LOGGER.error(
                "realtime window skipped",
                extra={
                    "event": "realtime_window_skipped",
                    "reason": "feature_generation_failed",
                    "frame_count": len(amplitudes),
                    "window_start": window.window_start,
                    "window_end": window.window_end,
                    "error": str(exc),
                },
            )
            return

        if self.dashboard_control.suppresses_realtime:
            self._status_event(
                "realtime_window_skipped",
                reason="baseline_collection_in_progress",
            )
            return

        self._status_event(
            "scores_updated",
            **payload["features"],
        )
        self._status_event(
            "realtime_payload_created",
        )
        try:
            artifact = self.preprocessed_writer.write(
                payload,
                room_id=self.config.room_id,
                window_start=window.window_start,
            )
            if artifact is not None:
                self._status_event(
                    "preprocessed_written",
                    path=str(artifact.local_path),
                )
                self.s3_uploader.upload_preprocessed(artifact)
                self._update_spool_counts()
        except Exception as exc:
            LOGGER.error(
                "preprocessed log handling failed",
                extra={
                    "event": "preprocessed_log_failed",
                    "window_id": payload["window_id"],
                    "error": str(exc),
                },
            )
        self.realtime_client.send(
            payload,
            frame_count=len(amplitudes),
        )
        self._update_spool_counts()

    def _status_event(self, event: str, **values: object) -> None:
        status = getattr(self, "status", None)
        if status is not None:
            status.record_event(event, **values)

    def _update_spool_counts(self) -> None:
        status = getattr(self, "status", None)
        if status is None:
            return
        try:
            s3_pending = len(self.s3_uploader.spool_store.list_pending())
            s3_pending += len(
                self.s3_uploader.spool_store.list_pending_single()
            )
            realtime_pending = len(
                self.realtime_client.spool_store.list_pending()
            )
            status.record_event(
                "spool_counts",
                s3_pending=s3_pending,
                realtime_pending=realtime_pending,
            )
        except Exception as exc:
            LOGGER.warning(
                "dashboard spool count failed",
                extra={
                    "event": "dashboard_status_update_failed",
                    "status_event": "spool_counts",
                    "error": str(exc),
                },
            )

    def _refresh_setup_context(
        self,
        *,
        force: bool = False,
    ) -> SetupState | None:
        setup = self.setup_state_manager.refresh(force=force)
        if setup is None:
            self.active_setup = None
            self.baseline_manager = None
            self._status_event(
                "setup_state_changed",
                setup_status="unavailable",
                baseline_id=None,
                calibration_id=None,
                baseline_state_path=None,
            )
            return None
        if (
            self.active_setup is None
            or self.active_setup.baseline_id != setup.baseline_id
            or self.active_setup.baseline_state_json
            != setup.baseline_state_json
        ):
            if getattr(self.config, "transport_test_enabled", False):
                self.baseline_manager = None
            else:
                self.baseline_manager = (
                    BaselineStateManager(
                        Path(setup.baseline_state_json),
                        setup.baseline_id,
                        reload_sec=self.config.baseline_reload_sec,
                        status_callback=self._baseline_status_callback,
                    )
                    if setup.baseline_id
                    else None
                )
            if self.baseline_manager is not None:
                self.baseline_manager.refresh(force=True)
        self.active_setup = setup
        self._status_event(
            "setup_state_changed",
            setup_status=setup.setup_status,
            baseline_id=setup.baseline_id,
            calibration_id=setup.calibration_id,
            baseline_state_path=setup.baseline_state_json,
        )
        return setup

    def _install_signal_handlers(self) -> None:
        def request_stop(
            signum: int,
            frame: object,
        ) -> None:
            LOGGER.info(
                "shutdown requested",
                extra={
                    "event": "shutdown_requested",
                    "signal": signum,
                },
            )
            self.stop_event.set()
            self.uart_reader.close()

        signal.signal(signal.SIGINT, request_stop)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, request_stop)


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        config = GatewayConfig.from_env()
    except ConfigurationError as exc:
        configure_logging("INFO")
        LOGGER.error(
            "configuration invalid",
            extra={
                "event": "config_invalid",
                "error": str(exc),
            },
        )
        return 1
    configure_logging(config.log_level)
    if args.check_config:
        return check_config(config)
    if args.command != "realtime":
        return run_setup_command(config, args)
    return GatewayApplication(config).run()


def run_setup_command(
    config: GatewayConfig,
    args: argparse.Namespace,
) -> int:
    client = SetupApiClient(
        config.model_server_url,
        config.model_server_timeout_sec,
    )
    state_store = SetupStateStore(config.local_setup_dir)
    spool_store = SetupSpoolStore(config.local_setup_spool_dir)
    try:
        if args.command == "room-create":
            client.create_room(
                build_room_create_payload(
                    config.room_id,
                    config.room_name,
                    config.device_group_id,
                )
            )
            now = utc_now()
            state_store.save(
                SetupState(
                    room_id=config.room_id,
                    device_group_id=config.device_group_id,
                    baseline_id=None,
                    calibration_id=None,
                    baseline_state_json=str(config.baseline_state_json),
                    server_url=config.model_server_url,
                    feature_version="stage1_feature_v1",
                    preprocessing_type="packet_mean_normalized",
                    setup_status="room_created",
                    created_at=now,
                    updated_at=now,
                )
            )
        elif args.command == "baseline-collect":
            BaselineCollector(
                config,
                client,
                state_store,
                spool_store,
            ).collect(args.duration_sec)
        elif args.command == "calibration-collect":
            CalibrationCollector(
                config,
                client,
                state_store,
                spool_store,
            ).collect(args.expected_windows)
        elif args.command == "transport-test-bootstrap":
            bootstrap_transport_test(config, client, state_store)
        else:
            raise ValueError(f"unsupported setup command: {args.command}")
    except (
        SetupApiError,
        BaselineCollectionError,
        CalibrationCollectionError,
        KeyError,
        OSError,
        ValueError,
    ) as exc:
        LOGGER.error(
            "setup command failed",
            extra={
                "event": "setup_command_failed",
                "command": args.command,
                "error": str(exc),
            },
        )
        return 1
    LOGGER.info(
        "setup command completed",
        extra={
            "event": "setup_command_completed",
            "command": args.command,
            "room_id": config.room_id,
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
