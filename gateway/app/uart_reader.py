"""Resilient byte-oriented UART reader with line recovery."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from threading import Event, Lock
from typing import Any, Callable, Iterator, Protocol

import serial

from app.models import SCHEMA_VERSION, ParseErrorRecord


LOGGER = logging.getLogger(__name__)


class SerialPort(Protocol):
    def read(self, size: int = 1) -> bytes: ...

    def write(self, data: bytes) -> int: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class UartEvent:
    gateway_ts: float
    line: str | None = None
    error_record: ParseErrorRecord | None = None


class UartReader:
    """Read UTF-8 UART lines while preserving partial input across timeouts."""

    def __init__(
        self,
        *,
        gateway_id: str,
        device_id: str,
        link_id: str,
        port: str,
        baudrate: int,
        timeout_sec: float,
        reconnect_sec: float = 5.0,
        max_line_bytes: int = 16384,
        error_capture_bytes: int = 512,
        read_size: int = 4096,
        serial_factory: Callable[..., SerialPort] = serial.Serial,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        status_callback: Callable[..., None] | None = None,
    ) -> None:
        if reconnect_sec < 1:
            raise ValueError("reconnect_sec must be at least 1")
        if max_line_bytes <= 0:
            raise ValueError("max_line_bytes must be greater than zero")
        if not 64 <= error_capture_bytes <= 4096:
            raise ValueError("error_capture_bytes must be between 64 and 4096")
        if read_size <= 0:
            raise ValueError("read_size must be greater than zero")

        self.gateway_id = gateway_id
        self.device_id = device_id
        self.link_id = link_id
        self.port = port
        self.baudrate = baudrate
        self.timeout_sec = timeout_sec
        self.reconnect_sec = reconnect_sec
        self.max_line_bytes = max_line_bytes
        self.error_capture_bytes = error_capture_bytes
        self.read_size = read_size
        self.serial_factory = serial_factory
        self.clock = clock
        self.sleep = sleep
        self.status_callback = status_callback
        self._buffer = bytearray()
        self._discard_until_newline = False
        self._serial: SerialPort | None = None
        self._serial_lock = Lock()

    def read_events(self, stop_event: Event) -> Iterator[UartEvent]:
        """Reconnect as needed and yield complete lines or UART errors."""

        while not stop_event.is_set():
            try:
                if self._serial is None:
                    serial_port = self.serial_factory(
                        port=self.port,
                        baudrate=self.baudrate,
                        timeout=self.timeout_sec,
                    )
                    with self._serial_lock:
                        self._serial = serial_port
                    LOGGER.info(
                        "serial connected",
                        extra={
                            "event": "serial_connected",
                            "port": self.port,
                            "baudrate": self.baudrate,
                        },
                    )
                    self._notify(
                        "serial_connected",
                        timestamp=self.clock(),
                    )

                serial_port = self._serial
                if serial_port is None:
                    continue
                chunk = serial_port.read(self.read_size)
                if chunk:
                    for event in self.feed_bytes(chunk):
                        self._notify(
                            "uart_line_received",
                            timestamp=event.gateway_ts,
                        )
                        yield event
            except (serial.SerialException, OSError) as exc:
                LOGGER.warning(
                    "serial disconnected",
                    extra={
                        "event": "serial_disconnected",
                        "port": self.port,
                        "error": str(exc),
                        "reconnect_sec": self.reconnect_sec,
                    },
                )
                self._notify(
                    "serial_disconnected",
                    timestamp=self.clock(),
                    error=str(exc),
                )
                self.close()
                if not stop_event.is_set():
                    self.sleep(self.reconnect_sec)

    def feed_bytes(
        self,
        chunk: bytes,
        *,
        gateway_ts: float | None = None,
    ) -> list[UartEvent]:
        """Consume one byte chunk; empty chunks preserve partial input."""

        if not isinstance(chunk, bytes):
            raise TypeError("chunk must be bytes")
        if not chunk:
            return []

        events: list[UartEvent] = []
        remaining = chunk
        while remaining:
            if self._discard_until_newline:
                newline_index = remaining.find(b"\n")
                if newline_index < 0:
                    return events
                self._discard_until_newline = False
                remaining = remaining[newline_index + 1 :]
                continue

            newline_index = remaining.find(b"\n")
            if newline_index < 0:
                self._buffer.extend(remaining)
                if len(self._buffer) > self.max_line_bytes:
                    events.append(
                        self._oversize_error(
                            bytes(self._buffer),
                            self._timestamp(gateway_ts),
                        )
                    )
                    self._buffer.clear()
                    self._discard_until_newline = True
                return events

            self._buffer.extend(remaining[:newline_index])
            line_bytes = bytes(self._buffer).removesuffix(b"\r")
            self._buffer.clear()
            timestamp = self._timestamp(gateway_ts)
            if len(line_bytes) > self.max_line_bytes:
                events.append(self._oversize_error(line_bytes, timestamp))
            else:
                events.append(self._decode_line(line_bytes, timestamp))
            remaining = remaining[newline_index + 1 :]

        return events

    def close(self) -> None:
        """Close the active serial port without discarding partial bytes."""

        with self._serial_lock:
            serial_port = self._serial
            self._serial = None
        if serial_port is not None:
            try:
                serial_port.close()
            finally:
                self._notify(
                    "serial_disconnected",
                    timestamp=self.clock(),
                )

    def write_line(self, line: str) -> None:
        """Write one ASCII command without interrupting the read loop."""

        if not line or "\r" in line or "\n" in line:
            raise ValueError("UART command must be one non-empty line")
        try:
            payload = f"{line}\n".encode("ascii")
        except UnicodeEncodeError as exc:
            raise ValueError("UART command must be ASCII") from exc

        with self._serial_lock:
            serial_port = self._serial
            if serial_port is None:
                raise serial.SerialException("serial port is not connected")
            written = serial_port.write(payload)
        if written != len(payload):
            raise serial.SerialTimeoutException(
                f"short UART write: {written}/{len(payload)} bytes"
            )

    def _decode_line(self, line_bytes: bytes, gateway_ts: float) -> UartEvent:
        try:
            line = line_bytes.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            return UartEvent(
                gateway_ts=gateway_ts,
                error_record=self._error_record(
                    gateway_ts=gateway_ts,
                    error=type(exc).__name__,
                    raw_bytes_hex=line_bytes.hex(),
                ),
            )
        return UartEvent(gateway_ts=gateway_ts, line=line)

    def _oversize_error(
        self,
        line_bytes: bytes,
        gateway_ts: float,
    ) -> UartEvent:
        captured = line_bytes[: self.error_capture_bytes]
        try:
            raw_line_truncated = captured.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return UartEvent(
                gateway_ts=gateway_ts,
                error_record=self._error_record(
                    gateway_ts=gateway_ts,
                    error="line exceeds SERIAL_MAX_LINE_BYTES",
                    raw_bytes_hex_truncated=captured.hex(),
                    exceeded_max_line_bytes=True,
                ),
            )
        return UartEvent(
            gateway_ts=gateway_ts,
            error_record=self._error_record(
                gateway_ts=gateway_ts,
                error="line exceeds SERIAL_MAX_LINE_BYTES",
                raw_line_truncated=raw_line_truncated,
                exceeded_max_line_bytes=True,
            ),
        )

    def _error_record(
        self,
        *,
        gateway_ts: float,
        error: str,
        raw_bytes_hex: str | None = None,
        raw_line_truncated: str | None = None,
        raw_bytes_hex_truncated: str | None = None,
        exceeded_max_line_bytes: bool | None = None,
    ) -> ParseErrorRecord:
        return ParseErrorRecord(
            schema_version=SCHEMA_VERSION,
            type="parse_error",
            gateway_id=self.gateway_id,
            device_id=self.device_id,
            link_id=self.link_id,
            gateway_ts=gateway_ts,
            error=error,
            raw_bytes_hex=raw_bytes_hex,
            raw_line_truncated=raw_line_truncated,
            raw_bytes_hex_truncated=raw_bytes_hex_truncated,
            exceeded_max_line_bytes=exceeded_max_line_bytes,
        )

    def _timestamp(self, gateway_ts: float | None) -> float:
        return self.clock() if gateway_ts is None else float(gateway_ts)

    def _notify(self, event: str, **values: Any) -> None:
        if self.status_callback is None:
            return
        try:
            self.status_callback(event, **values)
        except Exception:
            LOGGER.warning(
                "UART status callback failed",
                extra={
                    "event": "status_callback_failed",
                    "source": "uart_reader",
                    "status_event": event,
                },
            )
