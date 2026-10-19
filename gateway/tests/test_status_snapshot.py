import json
from pathlib import Path

from app.status_snapshot import GatewayStatus


class Clock:
    def __init__(self, value: float) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def make_status(clock: Clock) -> GatewayStatus:
    return GatewayStatus(
        gateway_id="gateway-laptop-001",
        device_id="esp32-rx-001",
        link_id="link-01",
        serial_port="COM3",
        aws_region="us-west-2",
        s3_upload_enabled=False,
        realtime_send_enabled=False,
        baseline_state_path=Path("data/baseline/baseline_state.json"),
        clock=clock,
    )


def test_snapshot_tracks_uart_window_and_quality_without_large_data() -> None:
    clock = Clock(1781395200.0)
    status = make_status(clock)

    status.record_event("serial_connected", timestamp=clock.value)
    status.record_event("uart_line_received", timestamp=clock.value)
    status.record_event("line_ignored")
    status.record_event(
        "frame_parsed",
        timestamp=clock.value,
        rssi=-46,
        noise_floor=-90,
        data=[1] * 384,
        raw_line="must not be stored",
    )
    clock.value += 0.5
    snapshot = status.snapshot()

    assert snapshot["uart"]["serial_connected"] is True
    assert snapshot["uart"]["total_lines"] == 1
    assert snapshot["uart"]["valid_frames"] == 1
    assert snapshot["uart"]["ignored_lines"] == 1
    assert snapshot["uart"]["fps_1s"] == 1.0
    assert snapshot["uart"]["fps_5s"] == 0.2
    assert snapshot["windows"]["current_realtime_frame_count"] == 1
    assert snapshot["csi_quality"]["latest_rssi"] == -46
    assert snapshot["csi_quality"]["latest_noise_floor"] == -90
    assert snapshot["s3"]["rawlog_enabled"] is True
    assert snapshot["s3"]["upload_enabled"] is False
    serialized = json.dumps(snapshot)
    assert "must not be stored" not in serialized
    assert '"data"' not in serialized


def test_completed_and_skipped_windows_reset_motion_state() -> None:
    clock = Clock(1781395200.0)
    status = make_status(clock)

    status.record_event(
        "motion_input_created",
        window_id="room_101_20260614T000000000Z",
        shape=[128, 192],
    )
    status.record_event(
        "realtime_window_completed",
        window_id="room_101_20260614T000005000Z",
        frame_count=127,
        minimum_packets_met=False,
        rssi_min=-50,
        rssi_max=-40,
        rssi_avg=-45.0,
        noise_floor_avg=-90.0,
    )
    status.record_event(
        "realtime_window_skipped",
        reason="insufficient_packets",
    )
    snapshot = status.snapshot()

    assert snapshot["windows"]["motion_input_created"] is False
    assert snapshot["windows"]["motion_input_shape"] is None
    assert snapshot["windows"]["last_skipped"] is True
    assert snapshot["windows"]["last_skip_reason"] == "insufficient_packets"
    assert snapshot["windows"]["minimum_packets_met"] is False


def test_setup_skip_preserves_created_motion_input() -> None:
    clock = Clock(1781395200.0)
    status = make_status(clock)
    status.record_event(
        "motion_input_created",
        window_id="room_101_20260614T000000000Z",
        shape=[128, 192],
    )

    status.record_event(
        "realtime_window_skipped",
        reason="setup_state_unavailable_or_not_ready",
    )
    snapshot = status.snapshot()

    assert snapshot["windows"]["motion_input_created"] is True
    assert snapshot["windows"]["motion_input_shape"] == [128, 192]


def test_baseline_and_delivery_events_are_summarized() -> None:
    clock = Clock(1781395200.0)
    status = make_status(clock)

    status.record_event(
        "baseline_state_loaded",
        timestamp=clock.value,
        baseline_id="baseline_room_101_v001",
    )
    status.record_event(
        "scores_updated",
        static_score=1.0,
        motion_score=2.0,
        shape_score=3.0,
        robust_max_shape_score=4.0,
    )
    status.record_event(
        "realtime_payload_send_failed",
        status_code=503,
        error="service unavailable",
    )
    status.record_event(
        "s3_upload_result",
        payload_type="rawlog_1m",
        success=False,
        error="upload failed",
    )
    status.record_event(
        "s3_upload_result",
        payload_type="preprocessed_payload",
        success=True,
        error=None,
    )
    status.record_event(
        "spool_counts",
        s3_pending=2,
        realtime_pending=3,
    )
    snapshot = status.snapshot()

    assert snapshot["baseline"]["loaded"] is True
    assert snapshot["baseline"]["valid"] is True
    assert snapshot["scores"]["robust_max_shape_score"] == 4.0
    assert snapshot["realtime"]["last_http_success"] is False
    assert snapshot["realtime"]["last_http_status_code"] == 503
    assert snapshot["s3"]["last_upload_success"] is False
    assert snapshot["s3"]["last_error"] == "upload failed"
    assert snapshot["s3"]["pending_spool_count"] == 2
    assert snapshot["realtime"]["pending_spool_count"] == 3


def test_firmware_ready_command_and_ack_are_summarized() -> None:
    clock = Clock(1781395200.0)
    status = make_status(clock)
    status.record_event(
        "firmware_ready",
        protocol_version=1,
        min_rate_hz=1,
        max_rate_hz=200,
        min_tx_power_qdbm=8,
        max_tx_power_qdbm=80,
        receiver_mac="aa:bb:cc:dd:ee:ff",
    )
    status.record_event(
        "firmware_command_requested",
        request_id=7,
        command="SET",
        rate_hz=40,
        tx_power_qdbm=60,
    )
    clock.value += 0.25
    status.record_event(
        "firmware_command_ack",
        timestamp=clock.value,
        request_id=7,
        status_code=0,
        rate_hz=40,
        tx_power_qdbm=60,
        error=None,
    )

    firmware = status.snapshot()["firmware"]

    assert firmware["protocol_ready"] is True
    assert firmware["min_tx_power_dbm"] == 2.0
    assert firmware["max_tx_power_dbm"] == 20.0
    assert firmware["command_pending"] is False
    assert firmware["last_command"] == "SET"
    assert firmware["actual_rate_hz"] == 40
    assert firmware["actual_tx_power_dbm"] == 15.0
    assert firmware["last_error"] is None

    status.record_event("serial_disconnected")
    disconnected = status.snapshot()["firmware"]
    assert disconnected["protocol_ready"] is False
    assert disconnected["command_pending"] is False


def test_write_snapshot_creates_compact_json_file(tmp_path: Path) -> None:
    clock = Clock(1781395200.0)
    status = make_status(clock)
    destination = tmp_path / "status" / "latest.json"

    assert status.write_snapshot(destination) is True

    text = destination.read_text(encoding="utf-8")
    document = json.loads(text)
    assert text.endswith("\n")
    assert document["schema_version"] == "1.0"
    assert document["gateway"]["gateway_id"] == "gateway-laptop-001"
    assert list(destination.parent.iterdir()) == [destination]
