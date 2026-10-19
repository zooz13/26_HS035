from __future__ import annotations

from typing import Iterable, List, Optional

from common.labels import STAGE1_EMPTY, STAGE1_OCCUPIED, STAGE1_UNKNOWN
from common.types import ProcessedWindow, StageResult


EMPTY_OCCUPIED_THRESHOLD = 0.10


def predict_stage1(window: ProcessedWindow) -> StageResult:
    score = window_score(window)
    if score is None:
        return StageResult(
            label=STAGE1_UNKNOWN,
            confidence=0.0,
            score=None,
            reason="No numeric features or matrix values were available.",
        )

    # Temporary pipeline-check heuristic only. Replace with a trained Stage 1 model in a later step.
    if score < EMPTY_OCCUPIED_THRESHOLD:
        confidence = _clamp(1.0 - score / EMPTY_OCCUPIED_THRESHOLD)
        return StageResult(
            label=STAGE1_EMPTY,
            confidence=confidence,
            score=score,
            reason="Mean absolute signal is below the dummy occupancy threshold.",
        )

    confidence = _clamp(min(1.0, score / (EMPTY_OCCUPIED_THRESHOLD * 4.0)))
    return StageResult(
        label=STAGE1_OCCUPIED,
        confidence=confidence,
        score=score,
        reason="Mean absolute signal is above the dummy occupancy threshold.",
    )


def window_score(window: ProcessedWindow) -> Optional[float]:
    values: List[float] = []
    if window.features is not None:
        values.extend(float(value) for value in window.features)
    elif window.data is not None:
        values.extend(_flatten_numeric(window.data))

    if not values:
        return None
    return sum(abs(value) for value in values) / len(values)


def _flatten_numeric(value: object) -> Iterable[float]:
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        yield float(value)
        return
    if isinstance(value, list):
        for item in value:
            yield from _flatten_numeric(item)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
