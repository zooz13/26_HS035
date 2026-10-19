"""Staging-only room bootstrap for testing Gateway-to-server delivery."""

from __future__ import annotations

from app.config import GatewayConfig
from app.csi_preprocessor import (
    NORMALIZATION_VERSION,
    STAGE1_FEATURE_VERSION,
)
from app.setup_api_client import SetupApiClient, SetupApiError
from app.setup_payload_builder import (
    build_baseline_start_payload,
    build_calibration_finish_payload,
    build_calibration_start_payload,
    build_room_create_payload,
    build_transport_test_baseline_finish_payload,
)
from app.setup_state import SetupState, SetupStateStore, utc_now


def transport_test_features(score: float) -> dict[str, float]:
    value = float(score)
    return {
        "static_score": value,
        "motion_score": value,
        "shape_score": value,
        "robust_max_shape_score": value,
    }


def bootstrap_transport_test(
    config: GatewayConfig,
    client: SetupApiClient,
    state_store: SetupStateStore,
) -> SetupState:
    try:
        status = client.setup_status(config.room_id)
    except SetupApiError:
        client.create_room(
            build_room_create_payload(
                config.room_id,
                config.room_name,
                config.device_group_id,
                "Gateway transport-test room",
            )
        )
        status = client.setup_status(config.room_id)

    baseline_id = status.get("baseline_id")
    if not baseline_id:
        baseline_start = client.baseline_start(
            config.room_id,
            build_baseline_start_payload(
                0.001,
                config.realtime_window_sec,
            ),
        )
        client.baseline_finish(
            config.room_id,
            build_transport_test_baseline_finish_payload(
                str(baseline_start["baseline_session_id"]),
                config.baseline_id,
                config.realtime_window_sec,
            ),
        )
        baseline_id = config.baseline_id

    status = client.setup_status(config.room_id)
    calibration_id = status.get("calibration_id")
    if not calibration_id:
        calibration_start = client.calibration_start(
            config.room_id,
            build_calibration_start_payload(
                str(baseline_id),
                0,
                config.realtime_window_sec,
                method="no_calibration",
            ),
        )
        calibration_finish = client.calibration_finish(
            config.room_id,
            build_calibration_finish_payload(
                str(calibration_start["calibration_session_id"]),
                method="no_calibration",
            ),
        )
        calibration_id = calibration_finish.get("calibration_id")
    if not calibration_id:
        status = client.setup_status(config.room_id)
        calibration_id = status.get("calibration_id")
    if not calibration_id:
        raise SetupApiError("transport-test bootstrap did not produce calibration_id")

    now = utc_now()
    existing = state_store.load(config.room_id)
    setup = SetupState(
        room_id=config.room_id,
        device_group_id=config.device_group_id,
        baseline_id=str(baseline_id),
        calibration_id=str(calibration_id),
        baseline_state_json=str(config.baseline_state_json),
        server_url=config.model_server_url,
        feature_version=STAGE1_FEATURE_VERSION,
        preprocessing_type=NORMALIZATION_VERSION,
        setup_status="ready",
        created_at=existing.created_at if existing else now,
        updated_at=now,
    )
    state_store.save(setup)
    return setup
