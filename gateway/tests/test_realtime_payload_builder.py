from app.realtime_payload_builder import (
    build_realtime_window_payload,
    build_window_id,
)


def test_builds_contract_payload_and_utc_window_id() -> None:
    payload = build_realtime_window_payload(
        window_id=build_window_id("room_101", 1781395205.0),
        baseline_id="baseline_room_101_v001",
        calibration_id="calib_room_101_v001",
        device_group_id="esp_group_101",
        window_start_time="2026-06-14T00:00:05Z",
        window_end_time="2026-06-14T00:00:10Z",
        window_duration_sec=5.0,
        features={
            "static_score": 1.0,
            "motion_score": 2.0,
            "shape_score": 3.0,
            "robust_max_shape_score": 3.0,
            "ignored": 9.0,
        },
        motion_input={
            "input_type": "window_matrix",
            "feature_version": "stage2_feature_v1",
            "shape": [128, 192],
            "values": [[0.0] * 192 for _ in range(128)],
        },
    )

    assert payload["window_id"] == "room_101_20260614T000005000Z"
    assert payload["data_type"] == "stage1_feature_vector"
    assert payload["feature_version"] == "stage1_feature_v1"
    assert sorted(payload["features"]) == [
        "motion_score",
        "robust_max_shape_score",
        "shape_score",
        "static_score",
    ]

