from pathlib import Path
from types import SimpleNamespace

import numpy as np

from app.baseline_state import load_baseline_state, save_baseline_state
from app.csi_preprocessor import build_baseline_state
from app.setup_cli import _update_setup_state
from app.setup_state import SetupState, SetupStateStore


def test_baseline_finish_resend_saves_server_ready_state(
    tmp_path: Path,
) -> None:
    store = SetupStateStore(tmp_path / "setup")
    store.save(
        SetupState(
            room_id="room_101",
            device_group_id="esp_group_101",
            baseline_id=None,
            calibration_id=None,
            baseline_state_json="data/baseline/baseline_state.json",
            server_url="http://127.0.0.1:8000",
            feature_version="stage1_feature_v1",
            preprocessing_type="packet_mean_normalized",
            setup_status="room_created",
            created_at="2026-06-14T00:00:00Z",
            updated_at="2026-06-14T00:00:00Z",
        )
    )
    config = SimpleNamespace(local_setup_dir=tmp_path / "setup")
    envelope = {
        "operation": "baseline_finish",
        "room_id": "room_101",
        "payload": {"baseline_id": "baseline_room_101_v001"},
    }
    response = {
        "status": "ready",
        "baseline_id": "baseline_room_101_v001",
        "calibration_id": "calib_room_101_baseline_default_v001",
        "calibration_method": "baseline_default_global_threshold",
    }

    _update_setup_state(config, envelope, response)

    setup = store.load("room_101")
    assert setup is not None
    assert setup.setup_status == "ready"
    assert setup.baseline_id == "baseline_room_101_v001"
    assert setup.calibration_id == (
        "calib_room_101_baseline_default_v001"
    )


def test_baseline_finish_resend_promotes_candidate_state(
    tmp_path: Path,
) -> None:
    store = SetupStateStore(tmp_path / "setup")
    active_path = tmp_path / "baseline" / "baseline_state.json"
    store.save(
        SetupState(
            room_id="room_101",
            device_group_id="esp_group_101",
            baseline_id=None,
            calibration_id=None,
            baseline_state_json=str(active_path),
            server_url="http://127.0.0.1:8000",
            feature_version="stage1_feature_v1",
            preprocessing_type="packet_mean_normalized",
            setup_status="room_created",
            created_at="2026-06-14T00:00:00Z",
            updated_at="2026-06-14T00:00:00Z",
        )
    )
    candidate = build_baseline_state(
        np.ones((128, 192), dtype=np.float32),
        baseline_id="baseline_room_101_v001",
        duration_sec=60,
        window_duration_sec=5,
    )
    candidate_path = save_baseline_state(
        tmp_path / "candidate" / "baseline_state.json",
        candidate,
    )
    config = SimpleNamespace(
        local_setup_dir=tmp_path / "setup",
        baseline_state_json=active_path,
    )
    envelope = {
        "operation": "baseline_finish",
        "room_id": "room_101",
        "payload": {"baseline_id": "baseline_room_101_v001"},
        "candidate_baseline_state_path": str(candidate_path),
    }
    response = {
        "status": "ready",
        "baseline_id": "baseline_room_101_v001",
        "calibration_id": "calib_room_101_baseline_default_v001",
    }

    _update_setup_state(config, envelope, response)

    assert load_baseline_state(active_path).baseline_id == (
        "baseline_room_101_v001"
    )
