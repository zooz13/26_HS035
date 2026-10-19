from __future__ import annotations

import math
import json
import sys
from pathlib import Path

import pytest
import numpy as np
from fastapi.testclient import TestClient


ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from api.server import create_app
from common.labels import RISK_NONE, RISK_UNKNOWN, STAGE1_EMPTY, STAGE1_OCCUPIED, STAGE2_SKIPPED, STAGE2_UNKNOWN
from common.types import RoomInferenceResult
from model_server.calibration_manager import calculate_empty_quantile_threshold
from model_server.input_validator import ValidationError, validate_processed_window_dict
from storage.state_store import StateStore


SAMPLE_WINDOW = {
    "window_id": "win_test_001",
    "device_group_id": "room_1",
    "device_ids": ["esp32_1", "esp32_2"],
    "start_time": "2026-06-13T15:24:08",
    "end_time": "2026-06-13T15:24:10",
    "data_type": "window_matrix",
    "shape": [128, 192],
    "data": [[0.05 if (row + col) % 17 == 0 else 0.0 for col in range(192)] for row in range(128)],
    "sampling_rate_hz": 50,
    "window_size": 128,
    "preprocess_version": "preprocess_v1",
    "normalization_version": "norm_v1_log_amp_empty_robust_zscore",
    "feature_version": "stage1_window_features_v1",
    "baseline_version": "baseline_v1",
    "source": "test",
    "metadata": {},
}


def test_processed_window_contract_accepts_sample_window() -> None:
    window = validate_processed_window_dict(SAMPLE_WINDOW)

    assert window.window_id == "win_test_001"
    assert window.data_type == "window_matrix"
    assert window.shape == [128, 192]


def test_processed_window_rejects_nan_features() -> None:
    payload = dict(SAMPLE_WINDOW)
    payload["data"] = [[0.0 for _ in range(192)] for _ in range(128)]
    payload["data"][0][0] = math.nan

    with pytest.raises(ValidationError):
        validate_processed_window_dict(payload)


def test_processed_window_rejects_feature_shape_mismatch() -> None:
    payload = dict(SAMPLE_WINDOW)
    payload["shape"] = [99, 192]

    with pytest.raises(ValidationError):
        validate_processed_window_dict(payload)


def test_api_health_window_and_latest_result() -> None:
    artifact_dir = ROOT_DIR / "artifacts" / "models" / "stage1" / "stage1_threshold_v001"
    if not (artifact_dir / "stage1_threshold_config.json").exists():
        pytest.skip("stage1_threshold_v001 artifact has not been generated yet")

    client = TestClient(create_app())

    health_response = client.get("/api/health")
    assert health_response.status_code == 200
    assert health_response.json()["status"] == "ok"

    latest_before = client.get("/api/result/latest")
    assert latest_before.status_code == 200
    assert latest_before.json()["status"] == "no_result"

    window_response = client.post("/api/window", json=SAMPLE_WINDOW)
    assert window_response.status_code == 200
    result = window_response.json()
    assert result["window_id"] == SAMPLE_WINDOW["window_id"]
    assert result["stage1_label"] in {STAGE1_EMPTY, STAGE1_OCCUPIED}
    assert result["stage2_label"] in {STAGE2_SKIPPED, STAGE2_UNKNOWN}
    assert result["risk_level"] in {RISK_NONE, RISK_UNKNOWN}
    assert result["model_version"] == "stage1_threshold_v001"

    latest_after = client.get("/api/result/latest")
    assert latest_after.status_code == 200
    assert latest_after.json()["result_id"] == result["result_id"]


def test_api_rejects_invalid_window() -> None:
    client = TestClient(create_app())
    payload = dict(SAMPLE_WINDOW)
    payload["device_ids"] = []

    response = client.post("/api/window", json=payload)

    assert response.status_code == 422
    assert "device_ids must not be empty" in str(response.json())


def test_room_window_stage2_static_moving_contract() -> None:
    client = TestClient(create_app())
    room_id, baseline_id, calibration_id, threshold = _prepare_ready_room(client, "room_stage2_contract")

    missing_stage2 = _room_window_payload("missing_stage2", baseline_id, calibration_id, threshold + 1.0)
    response = client.post(f"/api/rooms/{room_id}/window", json=missing_stage2)
    assert response.status_code == 400
    assert "motion_input is required" in response.text

    occupied_payload = _room_window_payload("occupied_with_stage2", baseline_id, calibration_id, threshold + 1.0)
    occupied_payload["motion_input"] = _stage2_input()
    response = client.post(f"/api/rooms/{room_id}/window", json=occupied_payload)
    assert response.status_code == 200
    occupied = response.json()
    assert occupied["stage1"]["label"] == "occupied"
    assert occupied["stage2"]["status"] == "ok"
    assert occupied["stage2"]["label"] in {"static_person", "moving_person"}
    assert occupied["stage2"]["artifact_id"] == "stage2_static_moving_v001"

    empty_payload = _room_window_payload("empty_without_stage2", baseline_id, calibration_id, threshold - 1.0)
    response = client.post(f"/api/rooms/{room_id}/window", json=empty_payload)
    assert response.status_code == 200
    empty = response.json()
    assert empty["stage1"]["label"] == "empty"
    assert empty["stage2"]["status"] == "skipped"
    assert empty["stage2"]["reason"] == "stage1_empty"

    latest = client.get(f"/api/rooms/{room_id}/result/latest")
    assert latest.status_code == 200
    assert latest.json()["latest_result"]["window_id"] == "empty_without_stage2"


def test_baseline_finish_creates_default_calibration_and_allows_realtime() -> None:
    client = TestClient(create_app())
    room_id = "room_baseline_default_ready"
    baseline_id = f"baseline_{room_id}_v001"
    threshold = 0.43

    client.post("/api/rooms", json={"room_id": room_id, "room_name": room_id, "device_group_id": "esp_group_test"})
    baseline_start = client.post(
        f"/api/rooms/{room_id}/baseline/start",
        json={"duration_sec": 300, "window_duration_sec": 5, "mode": "empty"},
    ).json()
    baseline_finish = client.post(
        f"/api/rooms/{room_id}/baseline/finish",
        json={
            "baseline_session_id": baseline_start["baseline_session_id"],
            "baseline_id": baseline_id,
            "duration_sec": 300,
            "window_duration_sec": 5,
            "num_windows": 60,
            "preprocess_version": "preprocess_v1",
            "normalization_version": "norm_v1_log_amp_empty_robust_zscore",
            "feature_version": "stage1_feature_v1",
            "baseline_profile": {"owner": "preprocessor", "summary": _baseline_summary(threshold)},
        },
    )

    assert baseline_finish.status_code == 200
    baseline_payload = baseline_finish.json()
    assert baseline_payload["status"] == "ready"
    assert baseline_payload["baseline_id"] == baseline_id
    assert baseline_payload["calibration_id"].startswith(f"calib_{room_id}_baseline_default_v")
    assert baseline_payload["calibration_method"] == "empty_quantile_threshold"
    assert baseline_payload["effective_threshold"] == pytest.approx(threshold)
    assert baseline_payload["source_metadata"]["baseline_session_id"] == baseline_start["baseline_session_id"]
    assert baseline_payload["source_metadata"]["quantile"] == pytest.approx(0.95)
    assert baseline_payload["source_metadata"]["summary_key"] == "p95_score"

    setup_status = client.get(f"/api/rooms/{room_id}/setup/status").json()
    assert setup_status["status"] == "ready"
    assert setup_status["baseline_id"] == baseline_id
    assert setup_status["calibration_id"] == baseline_payload["calibration_id"]
    assert setup_status["calibration_method"] == "empty_quantile_threshold"
    assert setup_status["effective_threshold"] == pytest.approx(threshold)

    window_payload = _room_window_payload("default_calibration_window", baseline_id, baseline_payload["calibration_id"], threshold - 1.0)
    response = client.post(f"/api/rooms/{room_id}/window", json=window_payload)
    assert response.status_code == 200
    result = response.json()
    assert result["stage1"]["label"] == "empty"
    assert result["stage1"]["threshold"] == threshold
    assert result["stage2"]["status"] == "skipped"


def test_baseline_finish_is_idempotent_per_room_and_session() -> None:
    app = create_app()
    client = TestClient(app)
    store = app.state.state_store
    room_id = "room_baseline_finish_idempotent"
    baseline_id = f"baseline_{room_id}_v001"

    client.post("/api/rooms", json={"room_id": room_id, "room_name": room_id, "device_group_id": "esp_group_test"})
    baseline_start = client.post(
        f"/api/rooms/{room_id}/baseline/start",
        json={"duration_sec": 300, "window_duration_sec": 5, "mode": "empty"},
    ).json()
    finish_payload = _baseline_finish_payload(baseline_start["baseline_session_id"], baseline_id)

    first = client.post(f"/api/rooms/{room_id}/baseline/finish", json=finish_payload)
    second = client.post(f"/api/rooms/{room_id}/baseline/finish", json=finish_payload)

    assert first.status_code == 200
    assert second.status_code == 200
    first_payload = first.json()
    second_payload = second.json()
    assert first_payload["baseline_session_id"] == baseline_start["baseline_session_id"]
    assert first_payload["idempotent"] is False
    assert second_payload["idempotent"] is True
    assert second_payload["baseline_id"] == first_payload["baseline_id"]
    assert second_payload["calibration_id"] == first_payload["calibration_id"]
    assert second_payload["effective_threshold"] == first_payload["effective_threshold"]
    assert len(store.baseline_profiles) == 1
    assert len(store.calibration_profiles) == 1
    assert len(store.completed_baseline_sessions) == 1
    completed = store.get_completed_baseline_session(room_id, baseline_start["baseline_session_id"])
    assert completed is not None
    assert completed.room_id == room_id
    assert completed.baseline_id == baseline_id
    assert completed.calibration_id == first_payload["calibration_id"]
    assert completed.status == "ready"
    assert completed.completed_at


def test_same_baseline_id_with_new_session_creates_one_new_result() -> None:
    app = create_app()
    client = TestClient(app)
    store = app.state.state_store
    room_id = "room_baseline_recollection"
    baseline_id = f"baseline_{room_id}_stable"

    client.post("/api/rooms", json={"room_id": room_id, "room_name": room_id, "device_group_id": "esp_group_test"})
    first_start = client.post(
        f"/api/rooms/{room_id}/baseline/start",
        json={"duration_sec": 300, "window_duration_sec": 5, "mode": "empty"},
    ).json()
    first = client.post(
        f"/api/rooms/{room_id}/baseline/finish",
        json=_baseline_finish_payload(first_start["baseline_session_id"], baseline_id, p95_score=0.41),
    ).json()

    second_start = client.post(
        f"/api/rooms/{room_id}/baseline/start",
        json={"duration_sec": 300, "window_duration_sec": 5, "mode": "empty"},
    ).json()
    stale_retry = client.post(
        f"/api/rooms/{room_id}/baseline/finish",
        json=_baseline_finish_payload(first_start["baseline_session_id"], baseline_id, p95_score=0.41),
    )
    assert stale_retry.status_code == 409
    assert len(store.calibration_profiles) == 1

    second = client.post(
        f"/api/rooms/{room_id}/baseline/finish",
        json=_baseline_finish_payload(second_start["baseline_session_id"], baseline_id, p95_score=0.87),
    ).json()

    assert second_start["baseline_session_id"] != first_start["baseline_session_id"]
    assert first["idempotent"] is False
    assert second["idempotent"] is False
    assert second["baseline_id"] == first["baseline_id"]
    assert second["calibration_id"] != first["calibration_id"]
    assert first["effective_threshold"] == pytest.approx(0.41)
    assert second["effective_threshold"] == pytest.approx(0.87)
    assert len(store.calibration_profiles) == 2
    assert len(store.completed_baseline_sessions) == 2
    assert store.get_baseline_profile(baseline_id).baseline_session_id == second_start["baseline_session_id"]

    stale_after_second_completed = client.post(
        f"/api/rooms/{room_id}/baseline/finish",
        json=_baseline_finish_payload(first_start["baseline_session_id"], baseline_id, p95_score=0.41),
    )
    assert stale_after_second_completed.status_code == 409
    assert client.get(f"/api/rooms/{room_id}/setup/status").json()["calibration_id"] == second["calibration_id"]


def test_baseline_summary_threshold_matches_existing_empty_quantile_calculation() -> None:
    client = TestClient(create_app())
    room_id = "room_baseline_quantile_equivalence"
    baseline_id = f"baseline_{room_id}_v001"
    scores = np.asarray([0.12, 0.18, 0.24, 0.31, 0.52, 0.75], dtype=np.float32)
    p95_score = float(np.quantile(scores, 0.95))
    expected = calculate_empty_quantile_threshold(scores.tolist(), 0.95, 0.0)

    client.post("/api/rooms", json={"room_id": room_id, "room_name": room_id, "device_group_id": "esp_group_test"})
    baseline_start = client.post(
        f"/api/rooms/{room_id}/baseline/start",
        json={"duration_sec": 300, "window_duration_sec": 5, "mode": "empty"},
    ).json()
    finish = client.post(
        f"/api/rooms/{room_id}/baseline/finish",
        json=_baseline_finish_payload(baseline_start["baseline_session_id"], baseline_id, p95_score=p95_score),
    )

    assert finish.status_code == 200
    assert finish.json()["effective_threshold"] == pytest.approx(expected)


@pytest.mark.parametrize(
    "summary, expected_detail",
    [
        ({}, "must include the exact 0.95 quantile"),
        ({"p95_score": "invalid"}, "must be a finite number"),
        ({"p95_score": True}, "must be a finite number"),
    ],
)
def test_baseline_finish_rejects_missing_or_invalid_quantile_summary(
    summary: dict,
    expected_detail: str,
) -> None:
    app = create_app()
    client = TestClient(app)
    store = app.state.state_store
    room_id = f"room_invalid_baseline_summary_{len(summary)}_{str(summary.get('p95_score'))}"
    baseline_id = f"baseline_{room_id}_v001"
    client.post("/api/rooms", json={"room_id": room_id, "room_name": room_id, "device_group_id": "esp_group_test"})
    baseline_start = client.post(
        f"/api/rooms/{room_id}/baseline/start",
        json={"duration_sec": 300, "window_duration_sec": 5, "mode": "empty"},
    ).json()
    payload = _baseline_finish_payload(baseline_start["baseline_session_id"], baseline_id)
    payload["baseline_profile"]["summary"] = summary

    finish = client.post(f"/api/rooms/{room_id}/baseline/finish", json=payload)

    assert finish.status_code == 400
    assert expected_detail in finish.json()["detail"]
    assert len(store.baseline_profiles) == 0
    assert len(store.calibration_profiles) == 0
    assert len(store.completed_baseline_sessions) == 0


def test_stale_baseline_finish_cannot_overwrite_active_or_completed_command() -> None:
    app = create_app()
    client = TestClient(app)
    store = app.state.state_store
    room_id = "room_stale_baseline_finish"
    stale_baseline_id = f"baseline_{room_id}_stale"
    active_baseline_id = f"baseline_{room_id}_active"

    client.post("/api/rooms", json={"room_id": room_id, "room_name": room_id, "device_group_id": "esp_group_test"})
    stale_start = client.post(
        f"/api/rooms/{room_id}/baseline/start",
        json={"duration_sec": 300, "window_duration_sec": 5, "mode": "empty"},
    ).json()
    stale_payload = _baseline_finish_payload(stale_start["baseline_session_id"], stale_baseline_id)
    stale_completed = client.post(f"/api/rooms/{room_id}/baseline/finish", json=stale_payload)
    assert stale_completed.status_code == 200

    active_start = client.post(
        f"/api/rooms/{room_id}/baseline/start",
        json={"duration_sec": 300, "window_duration_sec": 5, "mode": "empty"},
    ).json()

    stale_while_active = client.post(f"/api/rooms/{room_id}/baseline/finish", json=stale_payload)
    assert stale_while_active.status_code == 409
    assert len(store.baseline_profiles) == 1
    assert len(store.calibration_profiles) == 1

    active = client.post(
        f"/api/rooms/{room_id}/baseline/finish",
        json=_baseline_finish_payload(active_start["baseline_session_id"], active_baseline_id),
    )
    assert active.status_code == 200
    active_payload = active.json()

    stale_after_completed = client.post(f"/api/rooms/{room_id}/baseline/finish", json=stale_payload)
    assert stale_after_completed.status_code == 409
    setup_status = client.get(f"/api/rooms/{room_id}/setup/status").json()
    assert setup_status["status"] == "ready"
    assert setup_status["baseline_id"] == active_baseline_id
    assert setup_status["calibration_id"] == active_payload["calibration_id"]
    assert setup_status["baseline_command"]["room_id"] == room_id
    assert setup_status["baseline_command"]["command_id"] == active_start["command_id"]
    assert setup_status["baseline_command"]["baseline_session_id"] == active_start["baseline_session_id"]
    assert setup_status["baseline_command"]["status"] == "completed"
    assert setup_status["baseline_command"]["completed_at"] is not None
    assert len(store.baseline_profiles) == 2
    assert len(store.calibration_profiles) == 2
    assert len(store.completed_baseline_sessions) == 2


def test_baseline_command_is_persisted_and_duplicate_start_is_ignored() -> None:
    client = TestClient(create_app())
    room_id = "room_dashboard_command"
    client.post(
        "/api/rooms",
        json={
            "room_id": room_id,
            "room_name": room_id,
            "device_group_id": "esp_group_test",
        },
    )

    first = client.post(
        f"/api/rooms/{room_id}/baseline/start",
        json={"duration_sec": 300, "window_duration_sec": 5, "mode": "empty"},
    ).json()
    duplicate = client.post(
        f"/api/rooms/{room_id}/baseline/start",
        json={"duration_sec": 60, "window_duration_sec": 5, "mode": "empty"},
    ).json()
    status = client.get(f"/api/rooms/{room_id}/setup/status").json()

    assert first["accepted"] is True
    assert duplicate["accepted"] is False
    assert duplicate["ignored_reason"] == "baseline_collection_in_progress"
    assert duplicate["command_id"] == first["command_id"]
    assert status["baseline_command"]["duration_sec"] == 300

    acknowledged = client.post(
        f"/api/rooms/{room_id}/baseline/command/status",
        json={
            "command_id": first["command_id"],
            "status": "collecting",
            "gateway_id": "gateway-laptop-001",
        },
    )
    assert acknowledged.status_code == 200
    assert acknowledged.json()["status"] == "collecting"


def test_optional_calibration_finish_replaces_default_calibration_id() -> None:
    client = TestClient(create_app())
    room_id, baseline_id, default_calibration_id, _ = _prepare_default_ready_room(client, "room_optional_recalibration")

    calibration_start = client.post(
        f"/api/rooms/{room_id}/calibration/start",
        json={"baseline_id": baseline_id, "method": "guided_baseline_merge", "expected_windows": 1, "window_duration_sec": 5},
    ).json()
    client.post(
        f"/api/rooms/{room_id}/calibration/window",
        json={
            "calibration_session_id": calibration_start["calibration_session_id"],
            "window_id": "recalib_001",
            "baseline_id": baseline_id,
            "feature_version": "stage1_feature_v1",
            "features": {"static_score": 0.1, "motion_score": 0.1, "shape_score": 0.1, "robust_max_shape_score": 0.1},
        },
    )
    calibration = client.post(
        f"/api/rooms/{room_id}/calibration/finish",
        json={"calibration_session_id": calibration_start["calibration_session_id"], "method": "guided_baseline_merge"},
    )

    assert calibration.status_code == 200
    updated_calibration_id = calibration.json()["calibration_id"]
    assert updated_calibration_id != default_calibration_id
    setup_status = client.get(f"/api/rooms/{room_id}/setup/status").json()
    assert setup_status["status"] == "ready"
    assert setup_status["calibration_id"] == updated_calibration_id
    assert setup_status["calibration_method"] == "guided_baseline_merge"


def test_state_store_trims_room_result_history_to_configured_cap() -> None:
    store = StateStore(max_room_result_history=3)

    for idx in range(5):
        store.save_room_result(
            RoomInferenceResult(
                room_id="room_history_cap",
                window_id=f"win_{idx}",
                stage1={"label": "empty", "score": 0.1, "threshold": 2.5},
                stage2={"label": None, "status": "skipped", "reason": "stage1_empty"},
                final_status="empty",
                timestamp=f"2026-06-14T06:10:{idx:02d}+09:00",
            )
        )

    history = store.get_room_result_history("room_history_cap")
    assert [item.window_id for item in history] == ["win_2", "win_3", "win_4"]
    assert store.get_room_latest_result("room_history_cap").window_id == "win_4"


def test_room_window_updates_global_system_status() -> None:
    client = TestClient(create_app())
    room_id, baseline_id, calibration_id, threshold = _prepare_ready_room(client, "room_status_sync")

    before = client.get("/api/model/status").json()
    assert before["latest_window_id"] is None
    assert before["latest_inference_time"] is None
    # baseline registration must already flip baseline_status away from not_loaded
    assert before["baseline_status"] == "loaded"

    payload = _room_window_payload("status_sync_win", baseline_id, calibration_id, threshold - 1.0)
    response = client.post(f"/api/rooms/{room_id}/window", json=payload)
    assert response.status_code == 200
    result = response.json()

    after = client.get("/api/model/status").json()
    assert after["latest_window_id"] == "status_sync_win"
    assert after["latest_inference_time"] == result["timestamp"]


def _prepare_ready_room(client: TestClient, room_id: str) -> tuple[str, str, str, float]:
    baseline_id = f"baseline_{room_id}_v001"
    client.post("/api/rooms", json={"room_id": room_id, "room_name": room_id, "device_group_id": "esp_group_test"})
    baseline_start = client.post(
        f"/api/rooms/{room_id}/baseline/start",
        json={"duration_sec": 300, "window_duration_sec": 5, "mode": "empty"},
    ).json()
    client.post(
        f"/api/rooms/{room_id}/baseline/finish",
        json={
            "baseline_session_id": baseline_start["baseline_session_id"],
            "baseline_id": baseline_id,
            "duration_sec": 300,
            "window_duration_sec": 5,
            "num_windows": 60,
            "preprocess_version": "preprocess_v1",
            "normalization_version": "norm_v1_log_amp_empty_robust_zscore",
            "feature_version": "stage1_feature_v1",
            "baseline_profile": {"owner": "preprocessor", "summary": _baseline_summary(0.43)},
        },
    )
    calibration_start = client.post(
        f"/api/rooms/{room_id}/calibration/start",
        json={"baseline_id": baseline_id, "method": "guided_baseline_merge", "expected_windows": 1, "window_duration_sec": 5},
    ).json()
    client.post(
        f"/api/rooms/{room_id}/calibration/window",
        json={
            "calibration_session_id": calibration_start["calibration_session_id"],
            "window_id": "calib_001",
            "baseline_id": baseline_id,
            "feature_version": "stage1_feature_v1",
            "features": {"static_score": 0.1, "motion_score": 0.1, "shape_score": 0.1, "robust_max_shape_score": 0.1},
        },
    )
    calibration = client.post(
        f"/api/rooms/{room_id}/calibration/finish",
        json={"calibration_session_id": calibration_start["calibration_session_id"], "method": "guided_baseline_merge"},
    ).json()
    return room_id, baseline_id, calibration["calibration_id"], float(calibration["effective_threshold"])


def _prepare_default_ready_room(client: TestClient, room_id: str) -> tuple[str, str, str, float]:
    baseline_id = f"baseline_{room_id}_v001"
    client.post("/api/rooms", json={"room_id": room_id, "room_name": room_id, "device_group_id": "esp_group_test"})
    baseline_start = client.post(
        f"/api/rooms/{room_id}/baseline/start",
        json={"duration_sec": 300, "window_duration_sec": 5, "mode": "empty"},
    ).json()
    baseline = client.post(
        f"/api/rooms/{room_id}/baseline/finish",
        json={
            "baseline_session_id": baseline_start["baseline_session_id"],
            "baseline_id": baseline_id,
            "duration_sec": 300,
            "window_duration_sec": 5,
            "num_windows": 60,
            "preprocess_version": "preprocess_v1",
            "normalization_version": "norm_v1_log_amp_empty_robust_zscore",
            "feature_version": "stage1_feature_v1",
            "baseline_profile": {"owner": "preprocessor", "summary": _baseline_summary(0.43)},
        },
    ).json()
    return room_id, baseline_id, baseline["calibration_id"], float(baseline["effective_threshold"])


def _baseline_finish_payload(
    baseline_session_id: str,
    baseline_id: str,
    p95_score: float = 0.43,
) -> dict:
    return {
        "baseline_session_id": baseline_session_id,
        "baseline_id": baseline_id,
        "duration_sec": 300,
        "window_duration_sec": 5,
        "num_windows": 60,
        "preprocess_version": "preprocess_v1",
        "normalization_version": "norm_v1_log_amp_empty_robust_zscore",
        "feature_version": "stage1_feature_v1",
        "baseline_profile": {"owner": "preprocessor", "summary": _baseline_summary(p95_score)},
    }


def _baseline_summary(p95_score: float) -> dict[str, float]:
    return {
        "median_score": float(p95_score) * 0.50,
        "p90_score": float(p95_score) * 0.90,
        "p95_score": float(p95_score),
        "p99_score": float(p95_score) * 1.10,
    }


def _room_window_payload(window_id: str, baseline_id: str, calibration_id: str, score: float) -> dict:
    return {
        "window_id": window_id,
        "baseline_id": baseline_id,
        "calibration_id": calibration_id,
        "device_group_id": "esp_group_test",
        "window_start_time": "2026-06-14T06:10:00+09:00",
        "window_end_time": "2026-06-14T06:10:05+09:00",
        "window_duration_sec": 5,
        "data_type": "stage1_feature_vector",
        "feature_version": "stage1_feature_v1",
        "features": {"static_score": score, "motion_score": 0.1, "shape_score": 0.1, "robust_max_shape_score": score},
    }


def _stage2_input() -> dict:
    return {
        "input_type": "window_matrix",
        "feature_version": "stage2_feature_v1",
        "shape": [128, 192],
        "values": [[0.0 for _ in range(192)] for _ in range(128)],
    }


def _stage1_global_threshold() -> float:
    config_path = ROOT_DIR / "artifacts" / "models" / "stage1" / "stage1_threshold_v001" / "stage1_threshold_config.json"
    values = json.loads(config_path.read_text(encoding="utf-8"))
    return float(values["selected_threshold"])
