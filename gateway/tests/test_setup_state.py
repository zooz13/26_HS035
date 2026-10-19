from pathlib import Path

from app.setup_state import (
    SetupState,
    SetupStateManager,
    SetupStateStore,
)


def make_state(status: str = "ready") -> SetupState:
    return SetupState(
        room_id="room_101",
        device_group_id="esp_group_101",
        baseline_id="baseline_room_101_v001",
        calibration_id="calib_room_101_v001",
        baseline_state_json="data/baseline/baseline_state.json",
        server_url="http://127.0.0.1:8000",
        feature_version="stage1_feature_v1",
        preprocessing_type="packet_mean_normalized",
        setup_status=status,
        created_at="2026-06-14T00:00:00Z",
        updated_at="2026-06-14T00:00:00Z",
    )


def test_setup_state_round_trip_and_atomic_write(tmp_path: Path) -> None:
    store = SetupStateStore(tmp_path)

    path = store.save(make_state())
    loaded = store.load("room_101")

    assert path == tmp_path / "room_101.setup.json"
    assert loaded == make_state()
    assert list(tmp_path.iterdir()) == [path]


def test_setup_manager_missing_file_is_nonfatal(tmp_path: Path) -> None:
    manager = SetupStateManager(
        SetupStateStore(tmp_path),
        "room_101",
        clock=lambda: 0.0,
    )

    assert manager.refresh(force=True) is None


def test_setup_manager_reloads_changed_state(tmp_path: Path) -> None:
    store = SetupStateStore(tmp_path)
    store.save(make_state("baseline_ready"))
    times = iter([0.0, 11.0])
    manager = SetupStateManager(
        store,
        "room_101",
        reload_sec=10.0,
        clock=lambda: next(times),
    )

    assert manager.refresh(force=True).setup_status == "baseline_ready"
    store.save(make_state("ready"))

    assert manager.refresh().setup_status == "ready"
