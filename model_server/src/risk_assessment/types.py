from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class StrictModel(BaseModel):
    class Config:
        extra = "forbid"


class PredictionRecord(StrictModel):
    window_id: Optional[str] = None
    timestamp: str
    occupancy_state: str
    motion_state: str = "unknown"
    occupancy_confidence: Optional[float] = None
    motion_confidence: Optional[float] = None
    window_duration_sec: float = 5.0
    quality: Optional[float] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RiskAssessmentRequest(StrictModel):
    event_timestamp: str
    recent_predictions: Optional[List[PredictionRecord]] = None


class OccupancySummary(StrictModel):
    empty_ratio: float
    occupied_ratio: float
    recent_occupied: bool


class MotionSummary(StrictModel):
    static_ratio: float
    moving_ratio: float
    static_persistence_seconds: float


class RiskAssessmentResult(StrictModel):
    room_id: str
    event_timestamp: str
    residual_presence: str
    risk_level: str
    occupancy_summary: OccupancySummary
    motion_summary: MotionSummary
    confidence: float
    reason: str
    limitations: List[str] = Field(default_factory=list)
