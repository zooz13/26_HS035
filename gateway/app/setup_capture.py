"""UART capture runtime shared by baseline and calibration setup modes."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Callable

from app.config import GatewayConfig
from app.csi_parser import CsiParser, ParseStatus
from app.jsonl_writer import WindowFileWriter
from app.models import CanonicalCsiFrame, PayloadType
from app.s3_uploader import S3Uploader
from app.spool import SpoolStore
from app.uart_reader import UartReader
from app.window_buffer import CompletedWindow, WindowBuffer


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CaptureSummary:
    valid_frame_count: int
    parse_error_count: int
    ignored_line_count: int


class SetupCaptureRuntime:
    def __init__(self, config: GatewayConfig) -> None:
        self.config = config
        self.stop_event = threading.Event()
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
        )

    def run(
        self,
        *,
        duration_sec: float | None = None,
        on_frame: Callable[[CanonicalCsiFrame], None] | None = None,
        on_realtime_window: Callable[[CompletedWindow], bool] | None = None,
    ) -> CaptureSummary:
        timer: threading.Timer | None = None
        if duration_sec is not None:
            if duration_sec <= 0:
                raise ValueError("duration_sec must be greater than zero")
            timer = threading.Timer(duration_sec, self.stop_event.set)
            timer.daemon = True
            timer.start()
        try:
            for event in self.uart_reader.read_events(self.stop_event):
                if event.error_record is not None:
                    completed = self.window_buffer.add_parse_error(
                        event.error_record
                    )
                    self._process_completed(completed, on_realtime_window)
                    continue
                if event.line is None:
                    continue
                result = self.parser.parse_line(
                    event.line,
                    gateway_ts=event.gateway_ts,
                )
                if result.status is ParseStatus.PARSED and result.frame:
                    if on_frame is not None:
                        on_frame(result.frame)
                    completed = self.window_buffer.add_frame(result.frame)
                    if self._process_completed(
                        completed,
                        on_realtime_window,
                    ):
                        self.stop_event.set()
                elif (
                    result.status is ParseStatus.REJECTED
                    and result.error_record is not None
                ):
                    completed = self.window_buffer.add_parse_error(
                        result.error_record
                    )
                    self._process_completed(completed, on_realtime_window)
        except KeyboardInterrupt:
            self.stop_event.set()
            raise
        finally:
            if timer is not None:
                timer.cancel()
            self.uart_reader.close()
            self._process_completed(
                self.window_buffer.flush(),
                on_realtime_window,
                include_realtime=False,
            )
            self.s3_uploader.retry_pending()
        return CaptureSummary(
            valid_frame_count=self.parser.parsed_frame_count,
            parse_error_count=self.parser.parse_error_count,
            ignored_line_count=self.parser.ignored_line_count,
        )

    def _process_completed(
        self,
        windows: list[CompletedWindow],
        on_realtime_window: Callable[[CompletedWindow], bool] | None,
        *,
        include_realtime: bool = True,
    ) -> bool:
        should_stop = False
        for window in windows:
            if window.payload_type is PayloadType.RAWLOG_1M:
                written = self.rawlog_writer.write_window(window)
                self.s3_uploader.upload(written)
            elif (
                include_realtime
                and window.payload_type is PayloadType.REALTIME_WINDOW
                and on_realtime_window is not None
            ):
                should_stop = on_realtime_window(window) or should_stop
        return should_stop
