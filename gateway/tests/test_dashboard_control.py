from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from app.dashboard_control import DashboardBaselineController
from app.setup_state import SetupState


class Clock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value


class Frame:
    id = 1

    def to_dict(self) -> dict:
        return {"data": [3, 4] * 192}


def make_config(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        dashboard_control_enabled=True,
        remote_setup_poll_sec=2.0,
        baseline_collection_sec=300.0,
        model_server_url="http://127.0.0.1:8000",
        model_server_timeout_sec=20.0,
        local_setup_dir=tmp_path / "setup",
        local_setup_spool_dir=tmp_path / "setup_spool",
        room_id="room_101",
        gateway_id="gateway-laptop-001",
        device_group_id="esp_group_101",
        baseline_id="baseline_room_101_v001",
        baseline_state_json=tmp_path / "baseline_state.json",
        realtime_window_sec=5,
        amplitude_npy_enabled=False,
    )


def command(duration_sec: float = 300.0) -> dict:
    return {
        "command_id": "base_room_101_001",
        "baseline_session_id": "base_room_101_001",
        "status": "pending",
        "duration_sec": duration_sec,
        "window_duration_sec": 5,
    }


def test_remote_command_collects_from_existing_uart_stream_and_finishes(
    tmp_path: Path,
) -> None:
    api = Mock()
    api.setup_status.return_value = {"baseline_command": command(10)}
    clock = Clock()
    events: list[tuple[str, dict]] = []
    controller = DashboardBaselineController(
        make_config(tmp_path),
        api_client=api,
        clock=clock,
        status_callback=lambda event, **values: events.append((event, values)),
    )
    setup = SetupState(
        room_id="room_101",
        device_group_id="esp_group_101",
        baseline_id="baseline_room_101_v001",
        calibration_id="calib_room_101_v001",
        baseline_state_json=str(tmp_path / "baseline_state.json"),
        server_url="http://127.0.0.1:8000",
        feature_version="stage1_feature_v1",
        preprocessing_type="packet_mean_normalized",
        setup_status="ready",
        created_at="2026-06-20T00:00:00Z",
        updated_at="2026-06-20T00:00:00Z",
    )
    controller.collector.finish_collection = Mock(return_value=setup)

    controller.poll(force=True)
    for _ in range(128):
        controller.capture_frame(Frame())
    clock.value += 10

    assert controller.tick() is setup
    assert controller.mode == "realtime"
    api.baseline_command_status.assert_called_once_with(
        "room_101",
        command_id="base_room_101_001",
        status="collecting",
        gateway_id="gateway-laptop-001",
    )
    call = controller.collector.finish_collection.call_args.kwargs
    assert len(call["amplitudes"]) == 128
    assert call["duration_sec"] == 10
    assert any(event == "gateway_mode_changed" for event, _ in events)


def test_duplicate_command_does_not_restart_collection(tmp_path: Path) -> None:
    api = Mock()
    api.setup_status.return_value = {"baseline_command": command(300)}
    clock = Clock()
    controller = DashboardBaselineController(
        make_config(tmp_path),
        api_client=api,
        clock=clock,
    )

    controller.poll(force=True)
    clock.value += 100
    controller.poll(force=True)
    clock.value += 200
    controller.collector.finish_collection = Mock(
        side_effect=RuntimeError("expected test stop")
    )
    controller.tick()

    assert controller.collector.finish_collection.call_count == 1
    assert api.baseline_command_status.call_count == 2
