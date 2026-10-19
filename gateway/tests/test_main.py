from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.config import ConfigurationError
from app.main import (
    GatewayApplication,
    check_config,
    config_summary,
    main,
)
from app.models import PayloadType
from app.setup_state import SetupState
from app.window_buffer import CompletedWindow


def realtime_window(frame_count: int) -> CompletedWindow:
    record = {
        "id": 1,
        "data": [3, 4] * 192,
    }
    return CompletedWindow(
        payload_type=PayloadType.REALTIME_WINDOW,
        window_start=1781395200.0,
        window_end=1781395205.0,
        window_sec=5,
        records=tuple(record for _ in range(frame_count)),
        frame_count=frame_count,
        parse_error_count=0,
        rssi_min=-46,
        rssi_max=-46,
        rssi_avg=-46.0,
        noise_floor_avg=-90.0,
    )


def make_application() -> GatewayApplication:
    application = GatewayApplication.__new__(GatewayApplication)
    application.config = SimpleNamespace(
        room_id="room_101",
        baseline_id="baseline_room_101_v001",
        calibration_id="calib_room_101_v001",
        device_group_id="esp_group_101",
        transport_test_enabled=False,
        transport_test_stage1_score=3.0,
    )
    application.baseline_manager = Mock()
    setup = SetupState(
        room_id="room_101",
        device_group_id="esp_group_101",
        baseline_id="baseline_room_101_v001",
        calibration_id="calib_room_101_v001",
        baseline_state_json="data/baseline/baseline_state.json",
        server_url="http://127.0.0.1:8000",
        feature_version="stage1_feature_v1",
        preprocessing_type="packet_mean_normalized",
        setup_status="ready",
        created_at="2026-06-14T00:00:00Z",
        updated_at="2026-06-14T00:00:00Z",
    )
    application.active_setup = setup
    application.setup_state_manager = Mock()
    application.setup_state_manager.refresh.return_value = setup
    application.preprocessed_writer = Mock()
    application.s3_uploader = Mock()
    application.realtime_client = Mock()
    application.dashboard_control = Mock()
    application.dashboard_control.suppresses_realtime = False
    application.dashboard_control.mode = "realtime"
    return application


def test_short_realtime_window_does_not_send_or_spool() -> None:
    application = make_application()

    application._process_realtime_window(realtime_window(127))

    application.baseline_manager.refresh.assert_not_called()
    application.preprocessed_writer.write.assert_not_called()
    application.realtime_client.send.assert_not_called()


def test_missing_baseline_stops_after_amplitude_conversion() -> None:
    application = make_application()
    application.baseline_manager.refresh.return_value = None

    application._process_realtime_window(realtime_window(128))

    application.preprocessed_writer.write.assert_not_called()
    application.realtime_client.send.assert_not_called()


def test_missing_setup_still_builds_motion_input_but_does_not_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = make_application()
    application.setup_state_manager.refresh.return_value = None
    build_motion = Mock(
        return_value={
            "input_type": "window_matrix",
            "feature_version": "stage2_feature_v1",
            "shape": [128, 192],
            "values": [[0.0] * 192 for _ in range(128)],
        }
    )
    monkeypatch.setattr("app.main.build_motion_input", build_motion)

    application._process_realtime_window(realtime_window(128))

    build_motion.assert_called_once()
    application.preprocessed_writer.write.assert_not_called()
    application.realtime_client.send.assert_not_called()


def test_preprocessed_failure_does_not_block_http_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = make_application()
    application.baseline_manager.refresh.return_value = Mock()
    application.preprocessed_writer.write.side_effect = OSError("disk full")
    monkeypatch.setattr(
        "app.main.stage1_feature_vector",
        lambda *args: {
            "static_score": 1.0,
            "motion_score": 1.0,
            "shape_score": 1.0,
            "robust_max_shape_score": 1.0,
        },
    )
    monkeypatch.setattr(
        "app.main.build_motion_input",
        lambda *args: {
            "input_type": "window_matrix",
            "feature_version": "stage2_feature_v1",
            "shape": [128, 192],
            "values": [[0.0] * 192 for _ in range(128)],
        },
    )

    application._process_realtime_window(realtime_window(128))

    application.realtime_client.send.assert_called_once()


def test_transport_test_sends_real_motion_input_without_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = make_application()
    application.config.transport_test_enabled = True
    application.baseline_manager = None
    application.preprocessed_writer.write.return_value = None
    motion_input = {
        "input_type": "window_matrix",
        "feature_version": "stage2_feature_v1",
        "shape": [128, 192],
        "values": [[0.0] * 192 for _ in range(128)],
    }
    monkeypatch.setattr(
        "app.main.build_motion_input",
        Mock(return_value=motion_input),
    )

    application._process_realtime_window(realtime_window(128))

    payload = application.realtime_client.send.call_args.args[0]
    assert payload["motion_input"] is motion_input
    assert payload["features"] == {
        "static_score": 3.0,
        "motion_score": 3.0,
        "shape_score": 3.0,
        "robust_max_shape_score": 3.0,
    }


def test_config_summary_contains_only_allowlisted_non_secret_fields() -> None:
    config = SimpleNamespace(
        gateway_id="gateway-laptop-001",
        device_id="esp32-rx-001",
        link_id="link-01",
        serial_port="COM3",
        serial_baudrate=921600,
        s3_upload_enabled=False,
        realtime_send_enabled=False,
        transport_test_enabled=False,
        dashboard_control_enabled=False,
        aws_region="us-west-2",
        s3_bucket="actual-private-bucket",
        model_server_url="http://private-server",
    )

    summary = config_summary(config)

    assert summary == {
        "gateway_id": "gateway-laptop-001",
        "device_id": "esp32-rx-001",
        "link_id": "link-01",
        "serial_port": "COM3",
        "serial_baudrate": 921600,
        "s3_upload_enabled": False,
        "realtime_send_enabled": False,
        "transport_test_enabled": False,
        "dashboard_control_enabled": False,
        "aws_region": "us-west-2",
        "s3_bucket_configured": True,
    }
    serialized = repr(summary)
    assert "actual-private-bucket" not in serialized
    assert "private-server" not in serialized


def test_check_config_missing_baseline_is_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = SimpleNamespace(
        gateway_id="gateway-laptop-001",
        device_id="esp32-rx-001",
        link_id="link-01",
        serial_port="COM3",
        serial_baudrate=921600,
        s3_upload_enabled=False,
        realtime_send_enabled=False,
        transport_test_enabled=False,
        dashboard_control_enabled=False,
        aws_region="us-west-2",
        s3_bucket="placeholder",
        baseline_state_json="data/baseline/baseline_state.json",
        baseline_id="baseline_room_101_v001",
        baseline_reload_sec=10.0,
    )
    manager = Mock()
    manager.refresh.return_value = None
    manager_class = Mock(return_value=manager)
    monkeypatch.setattr("app.main.BaselineStateManager", manager_class)

    result = check_config(config)

    assert result == 0
    manager.refresh.assert_called_once_with(force=True)


def test_main_check_config_does_not_create_gateway_application(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = SimpleNamespace(log_level="INFO")
    monkeypatch.setattr(
        "app.main.GatewayConfig.from_env",
        Mock(return_value=config),
    )
    check = Mock(return_value=0)
    application = Mock()
    monkeypatch.setattr("app.main.check_config", check)
    monkeypatch.setattr("app.main.GatewayApplication", application)
    monkeypatch.setattr("app.main.configure_logging", Mock())

    result = main(["--check-config"])

    assert result == 0
    check.assert_called_once_with(config)
    application.assert_not_called()


def test_main_returns_one_for_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.main.GatewayConfig.from_env",
        Mock(side_effect=ConfigurationError("invalid setting")),
    )
    monkeypatch.setattr("app.main.configure_logging", Mock())

    assert main(["--check-config"]) == 1
