import json
import logging
from threading import Event
from unittest.mock import Mock

import serial
import pytest

from app.logging_config import JsonLogFormatter
from app.uart_reader import UartReader


def make_reader(**overrides: object) -> UartReader:
    values: dict[str, object] = {
        "gateway_id": "gateway-laptop-001",
        "device_id": "esp32-rx-001",
        "link_id": "link-01",
        "port": "COM3",
        "baudrate": 921600,
        "timeout_sec": 1.0,
        "clock": lambda: 1710000000.123,
    }
    values.update(overrides)
    return UartReader(**values)  # type: ignore[arg-type]


def test_partial_line_is_preserved_across_timeout() -> None:
    reader = make_reader()

    assert reader.feed_bytes(b"CSI_") == []
    assert reader.feed_bytes(b"") == []
    events = reader.feed_bytes(b"DATA\n")

    assert len(events) == 1
    assert events[0].line == "CSI_DATA"
    assert events[0].gateway_ts == 1710000000.123


def test_multiple_lines_in_one_chunk_are_emitted_in_order() -> None:
    events = make_reader().feed_bytes(b"first\r\nsecond\n")

    assert [event.line for event in events] == ["first", "second"]


def test_decode_error_is_returned_as_hex_parse_error() -> None:
    events = make_reader().feed_bytes(
        b"CSI_DATA\xff\n",
        gateway_ts=1710000000.5,
    )

    assert len(events) == 1
    error = events[0].error_record
    assert error is not None
    assert error.gateway_ts == 1710000000.5
    assert error.error == "UnicodeDecodeError"
    assert error.raw_bytes_hex == b"CSI_DATA\xff".hex()
    assert error.raw_line is None


def test_oversize_utf8_line_captures_prefix_and_enters_discard_mode() -> None:
    reader = make_reader(max_line_bytes=8, error_capture_bytes=64)

    events = reader.feed_bytes(b"123456789")
    recovered = reader.feed_bytes(b"discarded\nOK\n")

    assert len(events) == 1
    error = events[0].error_record
    assert error is not None
    assert error.exceeded_max_line_bytes is True
    assert error.raw_line_truncated == "123456789"
    assert error.raw_bytes_hex_truncated is None
    assert [event.line for event in recovered] == ["OK"]


def test_oversize_invalid_utf8_line_captures_hex_prefix() -> None:
    reader = make_reader(max_line_bytes=8, error_capture_bytes=64)

    events = reader.feed_bytes(b"\xff12345678")

    error = events[0].error_record
    assert error is not None
    assert error.exceeded_max_line_bytes is True
    assert error.raw_bytes_hex_truncated == b"\xff12345678".hex()
    assert error.raw_line_truncated is None


def test_complete_oversize_line_does_not_discard_following_line() -> None:
    reader = make_reader(max_line_bytes=8, error_capture_bytes=64)

    events = reader.feed_bytes(b"123456789\nNEXT\n")

    assert events[0].error_record is not None
    assert events[1].line == "NEXT"


def test_serial_connection_failure_retries_then_reads_line() -> None:
    stop_event = Event()
    serial_port = Mock()

    def read_and_stop(size: int) -> bytes:
        stop_event.set()
        return b"CSI_DATA\n"

    serial_port.read.side_effect = read_and_stop
    attempts = 0

    def serial_factory(**kwargs: object) -> Mock:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise serial.SerialException("not connected")
        return serial_port

    sleep = Mock()
    reader = make_reader(
        serial_factory=serial_factory,
        sleep=sleep,
        reconnect_sec=5.0,
    )

    events = list(reader.read_events(stop_event))

    assert [event.line for event in events] == ["CSI_DATA"]
    assert attempts == 2
    sleep.assert_called_once_with(5.0)


def test_close_closes_active_serial_port() -> None:
    stop_event = Event()
    serial_port = Mock()

    def read_and_stop(size: int) -> bytes:
        stop_event.set()
        return b""

    serial_port.read.side_effect = read_and_stop
    reader = make_reader(serial_factory=lambda **kwargs: serial_port)
    list(reader.read_events(stop_event))

    reader.close()

    serial_port.close.assert_called_once()


def test_write_line_uses_active_serial_port() -> None:
    stop_event = Event()
    serial_port = Mock()
    serial_port.write.return_value = len(b"CSI_CTRL,GET,7\n")

    def read_and_stop(size: int) -> bytes:
        stop_event.set()
        return b""

    serial_port.read.side_effect = read_and_stop
    reader = make_reader(serial_factory=lambda **kwargs: serial_port)
    list(reader.read_events(stop_event))

    reader.write_line("CSI_CTRL,GET,7")

    serial_port.write.assert_called_once_with(b"CSI_CTRL,GET,7\n")


def test_write_line_rejects_when_serial_is_not_connected() -> None:
    reader = make_reader()

    with pytest.raises(serial.SerialException, match="not connected"):
        reader.write_line("CSI_CTRL,GET,7")


def test_json_log_formatter_outputs_utc_event_and_extra_fields() -> None:
    formatter = JsonLogFormatter()
    record = logging.LogRecord(
        name="app.uart_reader",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="serial connected",
        args=(),
        exc_info=None,
    )
    record.created = 1710000000.123
    record.event = "serial_connected"
    record.port = "COM3"

    value = json.loads(formatter.format(record))

    assert value == {
        "ts": "2024-03-09T16:00:00.123Z",
        "level": "INFO",
        "logger": "app.uart_reader",
        "event": "serial_connected",
        "port": "COM3",
    }
