from app.models import (
    SCHEMA_VERSION,
    CanonicalCsiFrame,
    ParseErrorRecord,
    PayloadType,
)
from app.window_buffer import CompletedWindow, WindowBuffer


def make_frame(
    gateway_ts: float,
    *,
    frame_id: int = 1,
    rssi: int = -46,
    noise_floor: int = -90,
) -> CanonicalCsiFrame:
    return CanonicalCsiFrame(
        schema_version=SCHEMA_VERSION,
        type="csi_frame",
        gateway_id="gateway-laptop-001",
        device_id="esp32-rx-001",
        link_id="link-01",
        gateway_ts=gateway_ts,
        id=frame_id,
        mac="1a:00:00:00:00:00",
        rssi=rssi,
        rate=11,
        sig_mode=1,
        mcs=0,
        bandwidth=1,
        smoothing=1,
        not_sounding=1,
        aggregation=0,
        stbc=0,
        fec_coding=0,
        sgi=0,
        noise_floor=noise_floor,
        ampdu_cnt=0,
        channel=11,
        secondary_channel=2,
        local_timestamp=753582003,
        ant=0,
        sig_len=47,
        rx_format=1,
        len=2,
        first_word_invalid=0,
        csi_format="int8_interleaved_iq",
        data=(-17, -10),
        raw_line=f"CSI_DATA\t{frame_id}\t...",
    )


def make_error(gateway_ts: float) -> ParseErrorRecord:
    return ParseErrorRecord(
        schema_version=SCHEMA_VERSION,
        type="parse_error",
        gateway_id="gateway-laptop-001",
        device_id="esp32-rx-001",
        link_id="link-01",
        gateway_ts=gateway_ts,
        raw_line="CSI_DATA\tinvalid",
        error="CSI length mismatch",
    )


def by_type(
    windows: list[CompletedWindow],
    payload_type: PayloadType,
) -> CompletedWindow:
    return next(
        window for window in windows if window.payload_type is payload_type
    )


def test_frame_is_added_to_inference_and_rawlog_windows() -> None:
    buffer = WindowBuffer()
    buffer.add_frame(make_frame(1710000001.25))

    windows = buffer.flush()

    realtime = by_type(windows, PayloadType.REALTIME_WINDOW)
    rawlog = by_type(windows, PayloadType.RAWLOG_1M)
    assert realtime.window_start == 1710000000.0
    assert realtime.window_end == 1710000005.0
    assert rawlog.window_start == 1710000000.0
    assert rawlog.window_end == 1710000060.0
    assert realtime.frame_count == 1
    assert rawlog.frame_count == 1


def test_inference_excludes_raw_line_and_rawlog_includes_it() -> None:
    buffer = WindowBuffer()
    buffer.add_frame(make_frame(1710000001.25))

    windows = buffer.flush()

    realtime = by_type(windows, PayloadType.REALTIME_WINDOW)
    rawlog = by_type(windows, PayloadType.RAWLOG_1M)
    assert "raw_line" not in realtime.records[0]
    assert rawlog.records[0]["raw_line"] == "CSI_DATA\t1\t..."


def test_exact_boundary_closes_previous_realtime_window() -> None:
    buffer = WindowBuffer()
    buffer.add_frame(make_frame(1710000004.999, frame_id=1))

    completed = buffer.add_frame(make_frame(1710000005.0, frame_id=2))

    assert len(completed) == 1
    assert completed[0].payload_type is PayloadType.REALTIME_WINDOW
    assert completed[0].window_start == 1710000000.0
    assert completed[0].window_end == 1710000005.0
    assert completed[0].records[0]["id"] == 1
    remaining = buffer.flush()
    assert by_type(
        remaining, PayloadType.REALTIME_WINDOW
    ).records[0]["id"] == 2


def test_advance_time_closes_window_when_uart_is_idle() -> None:
    buffer = WindowBuffer()
    buffer.add_frame(make_frame(1710000001.0))

    completed = buffer.advance_time(1710000005.0)

    assert len(completed) == 1
    assert completed[0].payload_type is PayloadType.REALTIME_WINDOW


def test_parse_error_is_stored_only_in_rawlog() -> None:
    buffer = WindowBuffer()
    buffer.add_frame(make_frame(1710000001.0))
    buffer.add_parse_error(make_error(1710000002.0))

    windows = buffer.flush()

    realtime = by_type(windows, PayloadType.REALTIME_WINDOW)
    rawlog = by_type(windows, PayloadType.RAWLOG_1M)
    assert realtime.parse_error_count == 0
    assert len(realtime.records) == 1
    assert rawlog.parse_error_count == 1
    assert len(rawlog.records) == 2
    assert rawlog.records[1]["type"] == "parse_error"


def test_parse_error_only_window_emits_rawlog_not_inference() -> None:
    buffer = WindowBuffer()
    buffer.add_parse_error(make_error(1710000001.0))

    windows = buffer.flush()

    assert len(windows) == 1
    assert windows[0].payload_type is PayloadType.RAWLOG_1M
    assert windows[0].frame_count == 0
    assert windows[0].parse_error_count == 1
    assert windows[0].records[0]["type"] == "parse_error"


def test_extended_uart_error_fields_are_preserved_in_rawlog() -> None:
    buffer = WindowBuffer()
    buffer.add_parse_error(
        ParseErrorRecord(
            schema_version=SCHEMA_VERSION,
            type="parse_error",
            gateway_id="gateway-laptop-001",
            device_id="esp32-rx-001",
            link_id="link-01",
            gateway_ts=1710000001.0,
            error="line exceeds SERIAL_MAX_LINE_BYTES",
            raw_bytes_hex_truncated="ff00",
            exceeded_max_line_bytes=True,
        )
    )

    rawlog = by_type(buffer.flush(), PayloadType.RAWLOG_1M)

    assert rawlog.records[0]["raw_bytes_hex_truncated"] == "ff00"
    assert rawlog.records[0]["exceeded_max_line_bytes"] is True
    assert "raw_line" not in rawlog.records[0]


def test_statistics_use_only_normal_frames() -> None:
    buffer = WindowBuffer()
    buffer.add_frame(make_frame(1710000001.0, rssi=-50, noise_floor=-90))
    buffer.add_frame(make_frame(1710000002.0, rssi=-40, noise_floor=-80))
    buffer.add_parse_error(make_error(1710000003.0))

    realtime = by_type(
        buffer.flush(),
        PayloadType.REALTIME_WINDOW,
    )

    assert realtime.frame_count == 2
    assert realtime.rssi_min == -50
    assert realtime.rssi_max == -40
    assert realtime.rssi_avg == -45.0
    assert realtime.noise_floor_avg == -85.0
    assert realtime.estimated_fps == 0.4


def test_empty_buffer_does_not_emit_windows() -> None:
    buffer = WindowBuffer()

    assert buffer.advance_time(1710000060.0) == []
    assert buffer.flush() == []


def test_rawlog_closes_on_absolute_minute_boundary() -> None:
    buffer = WindowBuffer()
    buffer.add_frame(make_frame(1710000059.999))

    completed = buffer.advance_time(1710000060.0)

    rawlog = by_type(completed, PayloadType.RAWLOG_1M)
    assert rawlog.window_start == 1710000000.0
    assert rawlog.window_end == 1710000060.0
