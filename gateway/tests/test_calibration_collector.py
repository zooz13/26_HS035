from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np

from app.baseline_state import save_baseline_state
from app.calibration_collector import CalibrationCollector
from app.csi_preprocessor import build_baseline_state
from app.setup_capture import CaptureSummary
from app.setup_spool import SetupSpoolStore
from app.setup_state import SetupState, SetupStateStore
from app.window_buffer import CompletedWindow
from app.models import PayloadType


def config(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        room_id="room_101",
        realtime_window_sec=5,
        model_server_url="http://127.0.0.1:8000",
        local_setup_dir=tmp_path / "setup",
        amplitude_npy_enabled=False,
    )


def make_window(index: int) -> CompletedWindow:
    start = 1781395200.0 + index * 5
    record = {"data": [3, 4] * 192}
    return CompletedWindow(
        payload_type=PayloadType.REALTIME_WINDOW,
        window_start=start,
        window_end=start + 5,
        window_sec=5,
        records=tuple(record for _ in range(128)),
        frame_count=128,
        parse_error_count=0,
        rssi_min=-46,
        rssi_max=-46,
        rssi_avg=-46.0,
        noise_floor_avg=-90.0,
    )


def test_calibration_collector_posts_expected_windows_and_saves_id(
    tmp_path: Path,
) -> None:
    baseline_path = tmp_path / "baseline.json"
    baseline = build_baseline_state(
        np.ones((256, 192), dtype=np.float32),
        baseline_id="baseline_room_101_v001",
        duration_sec=60,
        window_duration_sec=5,
    )
    save_baseline_state(baseline_path, baseline)
    state_store = SetupStateStore(tmp_path / "setup")
    state_store.save(
        SetupState(
            room_id="room_101",
            device_group_id="esp_group_101",
            baseline_id="baseline_room_101_v001",
            calibration_id=None,
            baseline_state_json=str(baseline_path),
            server_url="http://127.0.0.1:8000",
            feature_version="stage1_feature_v1",
            preprocessing_type="packet_mean_normalized",
            setup_status="baseline_ready",
            created_at="2026-06-14T00:00:00Z",
            updated_at="2026-06-14T00:00:00Z",
        )
    )
    api = Mock()
    api.calibration_start.return_value = {
        "calibration_session_id": "calib_session_001"
    }
    api.calibration_finish.return_value = {
        "calibration_id": "calib_room_101_v001"
    }
    runtime = Mock()

    def run(**kwargs: object) -> CaptureSummary:
        callback = kwargs["on_realtime_window"]
        for index in range(12):
            assert callback(make_window(index)) is (index == 11)
        return CaptureSummary(1536, 0, 0)

    runtime.run.side_effect = run
    collector = CalibrationCollector(
        config(tmp_path),
        api,
        state_store,
        SetupSpoolStore(tmp_path / "spool"),
        capture_runtime=runtime,
    )

    ready = collector.collect(12)

    assert api.calibration_window.call_count == 12
    assert ready.setup_status == "ready"
    assert ready.calibration_id == "calib_room_101_v001"
    assert state_store.load("room_101").calibration_id == (
        "calib_room_101_v001"
    )
