from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from common.labels import (
    FINAL_OCCUPIED_NORMAL,
    FINAL_PRESENCE_DANGER,
    FINAL_PRESENCE_LOW,
    FINAL_PRESENCE_WARNING,
    FINAL_SAFE,
    FINAL_UNKNOWN,
    RISK_HIGH,
    RISK_LOW,
    RISK_MEDIUM,
    RISK_NONE,
    RISK_UNKNOWN,
    STAGE1_EMPTY,
    STAGE1_OCCUPIED,
    STAGE2_NORMAL,
    STAGE2_RISK_HIGH,
    STAGE2_RISK_LOW,
    STAGE2_RISK_MEDIUM,
)
from common.types import InferenceResult, ProcessedWindow, StageResult


DEFAULT_MODEL_VERSION = "dummy_model_server_v1"


def build_inference_result(
    window: ProcessedWindow,
    stage1_result: StageResult,
    stage2_result: StageResult,
    model_version: str = DEFAULT_MODEL_VERSION,
) -> InferenceResult:
    final_status, risk_level, message = aggregate_status(stage1_result, stage2_result)
    return InferenceResult(
        result_id=f"result_{uuid4().hex}",
        window_id=window.window_id,
        timestamp=datetime.now(timezone.utc).isoformat(),
        device_group_id=window.device_group_id,
        stage1_label=stage1_result.label,
        stage1_confidence=stage1_result.confidence,
        stage2_label=stage2_result.label,
        stage2_confidence=stage2_result.confidence,
        final_status=final_status,
        risk_level=risk_level,
        message=message,
        model_version=model_version,
        preprocess_version=window.preprocess_version,
        feature_version=window.feature_version,
    )


def aggregate_status(stage1_result: StageResult, stage2_result: StageResult) -> tuple[str, str, str]:
    if stage1_result.label == STAGE1_EMPTY:
        return FINAL_SAFE, RISK_NONE, "No presence detected in the processed window."

    if stage1_result.label == STAGE1_OCCUPIED and stage2_result.label == STAGE2_NORMAL:
        return FINAL_OCCUPIED_NORMAL, RISK_LOW, "Presence detected with normal risk classification."
    if stage1_result.label == STAGE1_OCCUPIED and stage2_result.label == STAGE2_RISK_LOW:
        return FINAL_PRESENCE_LOW, RISK_LOW, "Presence detected with low risk."
    if stage1_result.label == STAGE1_OCCUPIED and stage2_result.label == STAGE2_RISK_MEDIUM:
        return FINAL_PRESENCE_WARNING, RISK_MEDIUM, "Presence detected with medium risk."
    if stage1_result.label == STAGE1_OCCUPIED and stage2_result.label == STAGE2_RISK_HIGH:
        return FINAL_PRESENCE_DANGER, RISK_HIGH, "Presence detected with high risk."

    return FINAL_UNKNOWN, RISK_UNKNOWN, "The dummy pipeline could not produce a stable classification."
