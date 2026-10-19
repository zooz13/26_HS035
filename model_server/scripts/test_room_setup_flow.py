from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

try:
    from fastapi.testclient import TestClient
    from api.server import create_app

    HAS_FASTAPI = True
except ModuleNotFoundError:
    HAS_FASTAPI = False

from common.types import (  # noqa: E402
    BaselineFinishRequest,
    BaselineStartRequest,
    CalibrationFinishRequest,
    CalibrationStartRequest,
    CalibrationWindowRequest,
    RoomCreateRequest,
)
from model_server.calibration_manager import CalibrationManager  # noqa: E402
from model_server.room_manager import RoomManager  # noqa: E402
from storage.state_store import StateStore  # noqa: E402


ROOM_ID = "room_101"
BASELINE_ID = "baseline_room_101_v001"
FEATURE_VERSION = "stage1_feature_v1"


def main() -> int:
    if not HAS_FASTAPI:
        return manager_fallback_main()
    client = TestClient(create_app())

    assert_status(
        client.post(
            "/api/rooms",
            json={
                "room_id": ROOM_ID,
                "room_name": "실험실 101",
                "device_group_id": "esp_group_101",
                "description": "room setup flow smoke test",
            },
        ),
        200,
    )

    early_window = client.post(f"/api/rooms/{ROOM_ID}/window", json=room_window("early_win", 3.0, "missing_calib"))
    assert early_window.status_code == 400

    baseline_start = assert_status(
        client.post(
            f"/api/rooms/{ROOM_ID}/baseline/start",
            json={"duration_sec": 300, "window_duration_sec": 5, "mode": "empty", "requested_by": "dashboard"},
        ),
        200,
    ).json()
    baseline_session_id = baseline_start["baseline_session_id"]

    assert_status(
        client.post(
            f"/api/rooms/{ROOM_ID}/baseline/finish",
            json={
                "baseline_session_id": baseline_session_id,
                "baseline_id": BASELINE_ID,
                "duration_sec": 300,
                "window_duration_sec": 5,
                "num_windows": 60,
                "preprocess_version": "preprocess_v1",
                "normalization_version": "norm_v1_log_amp_empty_robust_zscore",
                "feature_version": FEATURE_VERSION,
                "baseline_profile": {
                    "owner": "preprocessor",
                    "subcarrier_count": 192,
                    "summary": {"median_score": 0.21, "p95_score": 0.43, "p99_score": 0.51},
                },
            },
        ),
        200,
    )

    calibration_start = assert_status(
        client.post(
            f"/api/rooms/{ROOM_ID}/calibration/start",
            json={
                "baseline_id": BASELINE_ID,
                "method": "guided_baseline_merge",
                "expected_windows": 3,
                "window_duration_sec": 5,
            },
        ),
        200,
    ).json()
    calibration_session_id = calibration_start["calibration_session_id"]

    for idx, score in enumerate([0.22, 0.41, 1.48], start=1):
        response = assert_status(
            client.post(
                f"/api/rooms/{ROOM_ID}/calibration/window",
                json={
                    "calibration_session_id": calibration_session_id,
                    "window_id": f"calib_win_{idx:04d}",
                    "baseline_id": BASELINE_ID,
                    "feature_version": FEATURE_VERSION,
                    "features": {
                        "static_score": score / 2.0,
                        "motion_score": 0.06,
                        "shape_score": score,
                        "robust_max_shape_score": score,
                    },
                },
            ),
            200,
        ).json()
        assert response["num_windows"] == idx

    calibration_finish = assert_status(
        client.post(
            f"/api/rooms/{ROOM_ID}/calibration/finish",
            json={"calibration_session_id": calibration_session_id, "method": "guided_baseline_merge", "quantile": 0.95, "margin": 0.0},
        ),
        200,
    ).json()
    calibration_id = calibration_finish["calibration_id"]
    threshold = float(calibration_finish["effective_threshold"])

    setup_status = assert_status(client.get(f"/api/rooms/{ROOM_ID}/setup/status"), 200).json()
    assert setup_status["status"] == "ready"

    occupied = assert_status(client.post(f"/api/rooms/{ROOM_ID}/window", json=room_window("win_occupied", threshold + 0.5, calibration_id, include_stage2=True)), 200).json()
    assert occupied["stage1"]["label"] == "occupied"
    assert occupied["stage2"]["label"] in {"static_person", "moving_person"}
    assert occupied["stage2"]["status"] == "ok"
    assert occupied["final_status"] in {"occupied_static_person", "occupied_moving_person"}

    empty = assert_status(client.post(f"/api/rooms/{ROOM_ID}/window", json=room_window("win_empty", threshold - 0.5, calibration_id)), 200).json()
    assert empty["stage1"]["label"] == "empty"
    assert empty["stage2"]["status"] == "skipped"

    latest = assert_status(client.get(f"/api/rooms/{ROOM_ID}/result/latest"), 200).json()
    assert latest["latest_result"]["window_id"] == "win_empty"

    print(
        json.dumps(
            {
                "room_id": ROOM_ID,
                "status": setup_status["status"],
                "threshold": threshold,
                "occupied_stage2": occupied["stage2"],
                "empty_stage2": empty["stage2"],
                "latest_window_id": latest["latest_result"]["window_id"],
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


def manager_fallback_main() -> int:
    store = StateStore(model_version="stage1_threshold_v001", model_status="threshold_ready")
    room_manager = RoomManager(store, stage1_artifact_id="stage1_threshold_v001")
    calibration_manager = CalibrationManager(store, stage1_artifact_id="stage1_threshold_v001", global_threshold=2.506834)

    room_manager.create_room(
        RoomCreateRequest(
            room_id=ROOM_ID,
            room_name="실험실 101",
            device_group_id="esp_group_101",
            description="room setup flow fallback smoke test",
        )
    )
    baseline_start = room_manager.start_baseline(
        ROOM_ID,
        BaselineStartRequest(duration_sec=300, window_duration_sec=5, mode="empty", requested_by="dashboard"),
    )
    completed, _, _ = room_manager.finish_baseline(
        ROOM_ID,
        BaselineFinishRequest(
            baseline_session_id=baseline_start["baseline_session_id"],
            baseline_id=BASELINE_ID,
            duration_sec=300,
            window_duration_sec=5,
            num_windows=60,
            preprocess_version="preprocess_v1",
            normalization_version="norm_v1_log_amp_empty_robust_zscore",
            feature_version=FEATURE_VERSION,
            baseline_profile={"owner": "preprocessor", "summary": {"p95_score": 0.43}},
        ),
        calibration_manager.build_default_calibration_profile,
    )
    session = calibration_manager.start_calibration(
        ROOM_ID,
        CalibrationStartRequest(baseline_id=completed.baseline_id, method="guided_baseline_merge", expected_windows=3, window_duration_sec=5),
    )
    for idx, score in enumerate([0.22, 0.41, 1.48], start=1):
        calibration_manager.add_calibration_window(
            ROOM_ID,
            CalibrationWindowRequest(
                calibration_session_id=session.calibration_session_id,
                window_id=f"calib_win_{idx:04d}",
                baseline_id=completed.baseline_id,
                feature_version=FEATURE_VERSION,
                features={"static_score": score / 2.0, "motion_score": 0.06, "shape_score": score, "robust_max_shape_score": score},
            ),
        )
    profile = calibration_manager.finish_calibration(
        ROOM_ID,
        CalibrationFinishRequest(calibration_session_id=session.calibration_session_id, method="guided_baseline_merge", quantile=0.95),
    )
    status = room_manager.setup_status(ROOM_ID, stage2_mode="disabled")
    assert status["status"] == "ready"
    print(
        json.dumps(
            {
                "mode": "manager_fallback_no_fastapi",
                "room_id": ROOM_ID,
                "status": status["status"],
                "calibration_id": profile.calibration_id,
                "effective_threshold": profile.effective_threshold,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


def room_window(window_id: str, score: float, calibration_id: str, include_stage2: bool = False) -> dict:
    payload = {
        "window_id": window_id,
        "baseline_id": BASELINE_ID,
        "calibration_id": calibration_id,
        "device_group_id": "esp_group_101",
        "window_start_time": "2026-06-14T06:10:00+09:00",
        "window_end_time": "2026-06-14T06:10:05+09:00",
        "window_duration_sec": 5,
        "data_type": "stage1_feature_vector",
        "feature_version": FEATURE_VERSION,
        "features": {
            "static_score": score / 3.0,
            "motion_score": 0.12,
            "shape_score": score,
            "robust_max_shape_score": score,
        },
    }
    if include_stage2:
        payload["stage2_input"] = {
            "input_type": "window_matrix",
            "feature_version": "stage2_feature_v1",
            "shape": [128, 192],
            "values": [[0.0 for _ in range(192)] for _ in range(128)],
        }
    return payload


def assert_status(response, status_code: int):
    if response.status_code != status_code:
        raise AssertionError(f"expected HTTP {status_code}, got {response.status_code}: {response.text}")
    return response


if __name__ == "__main__":
    raise SystemExit(main())
