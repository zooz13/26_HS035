from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from app.baseline_collector import BaselineCollectionError, BaselineCollector
from app.baseline_state import load_baseline_state
from app.csi_preprocessor import count_windows
from app.setup_api_client import SetupApiError
from app.setup_capture import CaptureSummary
from app.setup_spool import SetupSpoolStore
from app.setup_state import SetupStateStore


class Frame:
    def __init__(self, frame_id: int) -> None:
        self.id = frame_id

    def to_dict(self) -> dict:
        return {"data": [3, 4] * 192}


def config(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        room_id="room_101",
        device_group_id="esp_group_101",
        baseline_id="baseline_room_101_v001",
        baseline_state_json=tmp_path / "baseline_state.json",
        realtime_window_sec=5,
        model_server_url="http://127.0.0.1:8000",
        local_setup_dir=tmp_path / "setup",
        amplitude_npy_enabled=False,
    )


def test_count_windows_uses_packet_window_and_stride() -> None:
    assert count_windows(127, 128, 64) == 0
    assert count_windows(128, 128, 64) == 1
    assert count_windows(256, 128, 64) == 3


def test_baseline_collector_builds_state_and_finish_payload(
    tmp_path: Path,
) -> None:
    api = Mock()
    api.baseline_start.return_value = {
        "baseline_session_id": "base_001"
    }
    api.baseline_finish.return_value = {
        "status": "ready",
        "baseline_id": "baseline_room_101_v001",
        "calibration_id": "calib_room_101_baseline_default_v001",
        "calibration_method": "baseline_default_global_threshold",
    }
    runtime = Mock()

    def run(**kwargs: object) -> CaptureSummary:
        callback = kwargs["on_frame"]
        for index in range(256):
            callback(Frame(index))
        return CaptureSummary(256, 0, 0)

    runtime.run.side_effect = run
    state_store = SetupStateStore(tmp_path / "setup")
    collector = BaselineCollector(
        config(tmp_path),
        api,
        state_store,
        SetupSpoolStore(tmp_path / "spool"),
        capture_runtime=runtime,
    )

    setup = collector.collect(60)
    baseline = load_baseline_state(tmp_path / "baseline_state.json")
    payload = api.baseline_finish.call_args.args[1]

    assert baseline.num_windows == 3
    assert baseline.preprocessing_type == "packet_mean_normalized"
    assert payload["num_windows"] == 3
    assert payload["baseline_profile"]["summary"]["valid_packet_count"] == 256
    assert setup.setup_status == "ready"
    assert setup.calibration_id == (
        "calib_room_101_baseline_default_v001"
    )


def test_baseline_collector_supports_legacy_baseline_ready_server(
    tmp_path: Path,
) -> None:
    api = Mock()
    api.baseline_start.return_value = {
        "baseline_session_id": "base_legacy"
    }
    api.baseline_finish.return_value = {
        "status": "baseline_ready",
        "baseline_id": "baseline_room_101_v001",
    }
    api.setup_status.return_value = {
        "status": "baseline_ready",
        "baseline_id": "baseline_room_101_v001",
        "calibration_id": None,
    }
    runtime = Mock()

    def run(**kwargs: object) -> CaptureSummary:
        callback = kwargs["on_frame"]
        for index in range(128):
            callback(Frame(index))
        return CaptureSummary(128, 0, 0)

    runtime.run.side_effect = run
    collector = BaselineCollector(
        config(tmp_path),
        api,
        SetupStateStore(tmp_path / "setup"),
        SetupSpoolStore(tmp_path / "spool"),
        capture_runtime=runtime,
    )

    setup = collector.collect(60)

    assert setup.setup_status == "baseline_ready"
    assert setup.calibration_id is None
    api.setup_status.assert_called_once_with("room_101")


def test_failed_finish_keeps_candidate_without_activating_it(
    tmp_path: Path,
) -> None:
    api = Mock()
    api.baseline_finish.side_effect = SetupApiError("offline")
    spool_store = SetupSpoolStore(tmp_path / "spool")
    collector = BaselineCollector(
        config(tmp_path),
        api,
        SetupStateStore(tmp_path / "setup"),
        spool_store,
    )
    amplitudes = [
        np.asarray([3.0, 4.0] * 96, dtype=np.float32)
        for _ in range(128)
    ]

    with pytest.raises(BaselineCollectionError, match="offline"):
        collector.finish_collection(
            session_id="base_failed",
            duration_sec=60,
            amplitudes=amplitudes,
        )

    assert not (tmp_path / "baseline_state.json").exists()
    pending = spool_store.list_pending()
    assert len(pending) == 1
    envelope = spool_store.load_envelope(pending[0])
    candidate_path = Path(envelope["candidate_baseline_state_path"])
    assert candidate_path.exists()
    assert load_baseline_state(candidate_path).baseline_id == (
        "baseline_room_101_v001"
    )
