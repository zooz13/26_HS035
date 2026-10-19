from __future__ import annotations

from common.labels import (
    STAGE1_EMPTY,
    STAGE1_OCCUPIED,
    STAGE2_NORMAL,
    STAGE2_RISK_HIGH,
    STAGE2_RISK_LOW,
    STAGE2_RISK_MEDIUM,
    STAGE2_UNKNOWN,
)
from common.types import ProcessedWindow, StageResult
from model_server.dummy_stage1 import window_score


RISK_LOW_THRESHOLD = 0.35
RISK_MEDIUM_THRESHOLD = 0.70


def predict_stage2(window: ProcessedWindow, stage1_result: StageResult) -> StageResult:
    if stage1_result.label == STAGE1_EMPTY:
        return StageResult(
            label=STAGE2_NORMAL,
            confidence=max(0.5, stage1_result.confidence),
            score=stage1_result.score,
            reason="Stage 1 marked the window empty; Stage 2 is bypassed as normal.",
        )

    if stage1_result.label != STAGE1_OCCUPIED:
        return StageResult(
            label=STAGE2_UNKNOWN,
            confidence=0.0,
            score=stage1_result.score,
            reason="Stage 1 did not provide an occupied window for risk classification.",
        )

    score = stage1_result.score
    if score is None:
        score = window_score(window)
    if score is None:
        return StageResult(
            label=STAGE2_UNKNOWN,
            confidence=0.0,
            score=None,
            reason="No numeric score was available for dummy risk classification.",
        )

    # Temporary pipeline-check heuristic only. Replace with a trained Stage 2 model in a later step.
    if score < RISK_LOW_THRESHOLD:
        return StageResult(label=STAGE2_RISK_LOW, confidence=_confidence(score, RISK_LOW_THRESHOLD), score=score)
    if score < RISK_MEDIUM_THRESHOLD:
        return StageResult(
            label=STAGE2_RISK_MEDIUM,
            confidence=_confidence(score - RISK_LOW_THRESHOLD, RISK_MEDIUM_THRESHOLD - RISK_LOW_THRESHOLD),
            score=score,
        )
    return StageResult(label=STAGE2_RISK_HIGH, confidence=_confidence(score, 1.0), score=score)


def _confidence(value: float, scale: float) -> float:
    if scale <= 0:
        return 0.5
    return max(0.0, min(1.0, 0.5 + (value / scale) * 0.5))
