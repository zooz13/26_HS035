import json
import os
from pathlib import Path

from app.baseline_state import BaselineStateManager


def baseline_json(baseline_id: str) -> dict[str, object]:
    return {
        "baseline_id": baseline_id,
        "segment": "full_buffer",
        "preprocessing_type": "packet_mean_normalized",
        "window_size": 128,
        "stride": 64,
        "median": [1.0] * 192,
        "robust_scale": [1.0] * 192,
        "diff_abs_p95_scale": 1.0,
        "temporal_std_p95_scale": 1.0,
        "baseline_amp_mean": [1.0] * 192,
        "baseline_amp_median": [1.0] * 192,
        "baseline_amp_std": [0.0] * 192,
        "baseline_shape": [0.0] * 192,
        "num_windows": 1,
        "duration_sec": 300.0,
        "window_duration_sec": 5.0,
    }


def test_missing_baseline_returns_none_without_raising(tmp_path: Path) -> None:
    manager = BaselineStateManager(
        tmp_path / "missing.json",
        "baseline_room_101_v001",
        clock=lambda: 0.0,
    )

    assert manager.refresh(force=True) is None


def test_baseline_id_mismatch_is_invalid(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    path.write_text(
        json.dumps(baseline_json("wrong")),
        encoding="utf-8",
    )
    manager = BaselineStateManager(
        path,
        "baseline_room_101_v001",
        clock=lambda: 0.0,
    )

    assert manager.refresh(force=True) is None


def test_manager_reloads_when_mtime_changes(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    path.write_text(
        json.dumps(baseline_json("baseline_room_101_v001")),
        encoding="utf-8",
    )
    times = iter([0.0, 11.0])
    manager = BaselineStateManager(
        path,
        "baseline_room_101_v001",
        reload_sec=10.0,
        clock=lambda: next(times),
    )
    first = manager.refresh(force=True)
    assert first is not None

    value = baseline_json("baseline_room_101_v001")
    value["duration_sec"] = 600.0
    path.write_text(json.dumps(value), encoding="utf-8")
    stat = path.stat()
    os.utime(
        path,
        ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000),
    )

    second = manager.refresh()

    assert second is not None
    assert second.duration_sec == 600.0

