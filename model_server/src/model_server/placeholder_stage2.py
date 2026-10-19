from __future__ import annotations

from common.labels import STAGE1_EMPTY, STAGE2_SKIPPED, STAGE2_UNKNOWN
from common.types import ProcessedWindow, StageResult


def predict_stage2_unknown(window: ProcessedWindow, stage1_result: StageResult) -> StageResult:
    del window
    if stage1_result.label == STAGE1_EMPTY:
        return StageResult(
            label=STAGE2_SKIPPED,
            confidence=1.0,
            score=stage1_result.score,
            reason="Stage 1 marked the window empty; Stage 2 static/moving classification was skipped.",
        )
    return StageResult(
        label=STAGE2_UNKNOWN,
        confidence=0.0,
        score=stage1_result.score,
        reason="Stage 2 static/moving classifier is not connected for this server mode.",
    )
