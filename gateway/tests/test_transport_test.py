from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from app.transport_test import (
    bootstrap_transport_test,
    transport_test_features,
)


def config(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        room_id="room_101",
        room_name="Room 101",
        device_group_id="esp_group_101",
        realtime_window_sec=5,
        baseline_id="baseline_room_101_transport_test",
        baseline_state_json=tmp_path / "baseline_state.json",
        model_server_url="http://127.0.0.1:8000",
    )


def test_transport_test_features_use_fixed_finite_score() -> None:
    assert transport_test_features(3.0) == {
        "static_score": 3.0,
        "motion_score": 3.0,
        "shape_score": 3.0,
        "robust_max_shape_score": 3.0,
    }


def test_bootstrap_creates_ready_room_with_no_calibration(
    tmp_path: Path,
) -> None:
    client = Mock()
    client.setup_status.side_effect = [
        RuntimeError("room missing"),
        {},
        {"baseline_id": "baseline_room_101_transport_test"},
    ]
    client.baseline_start.return_value = {
        "baseline_session_id": "base_test"
    }
    client.calibration_start.return_value = {
        "calibration_session_id": "calib_session_test"
    }
    client.calibration_finish.return_value = {
        "calibration_id": "calib_room_101_001"
    }
    state_store = Mock()
    state_store.load.return_value = None

    # The production client raises SetupApiError for a missing room.
    from app.setup_api_client import SetupApiError

    client.setup_status.side_effect = [
        SetupApiError("HTTP 404"),
        {},
        {"baseline_id": "baseline_room_101_transport_test"},
    ]

    setup = bootstrap_transport_test(
        config(tmp_path),
        client,
        state_store,
    )

    assert setup.setup_status == "ready"
    assert setup.baseline_id == "baseline_room_101_transport_test"
    assert setup.calibration_id == "calib_room_101_001"
    client.create_room.assert_called_once()
    assert client.calibration_start.call_args.args[1]["method"] == (
        "no_calibration"
    )
    assert client.calibration_start.call_args.args[1]["expected_windows"] == 0
    assert client.calibration_finish.call_args.args[1]["method"] == (
        "no_calibration"
    )
    state_store.save.assert_called_once_with(setup)


def test_bootstrap_uses_default_calibration_created_by_baseline_finish(
    tmp_path: Path,
) -> None:
    client = Mock()
    from app.setup_api_client import SetupApiError

    client.setup_status.side_effect = [
        SetupApiError("HTTP 404"),
        {
            "status": "uncalibrated",
            "baseline_id": None,
            "calibration_id": None,
        },
        {
            "status": "ready",
            "baseline_id": "baseline_room_101_transport_test",
            "calibration_id": (
                "calib_room_101_baseline_default_v001"
            ),
            "calibration_method": (
                "baseline_default_global_threshold"
            ),
        },
    ]
    client.baseline_start.return_value = {
        "baseline_session_id": "base_test"
    }
    client.baseline_finish.return_value = {
        "status": "ready",
        "baseline_id": "baseline_room_101_transport_test",
        "calibration_id": "calib_room_101_baseline_default_v001",
    }
    state_store = Mock()
    state_store.load.return_value = None

    setup = bootstrap_transport_test(
        config(tmp_path),
        client,
        state_store,
    )

    assert setup.setup_status == "ready"
    assert setup.calibration_id == (
        "calib_room_101_baseline_default_v001"
    )
    client.calibration_start.assert_not_called()
    client.calibration_finish.assert_not_called()


def test_bootstrap_reuses_already_ready_server_state(
    tmp_path: Path,
) -> None:
    client = Mock()
    ready = {
        "status": "ready",
        "baseline_id": "baseline_existing",
        "calibration_id": "calib_existing",
    }
    client.setup_status.side_effect = [ready, ready]
    state_store = Mock()
    state_store.load.return_value = None

    setup = bootstrap_transport_test(
        config(tmp_path),
        client,
        state_store,
    )

    assert setup.baseline_id == "baseline_existing"
    assert setup.calibration_id == "calib_existing"
    client.create_room.assert_not_called()
    client.baseline_start.assert_not_called()
    client.calibration_start.assert_not_called()
    client.calibration_finish.assert_not_called()
