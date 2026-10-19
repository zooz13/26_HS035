from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class StrictModel(BaseModel):
    class Config:
        extra = "forbid"


class PresignUploadRequest(StrictModel):
    key: str
    content_type: str
    content_encoding: Optional[str] = None


class PresignUploadResponse(StrictModel):
    url: str
    method: str
    headers: Dict[str, str]
    expires_in: int


class ProcessedWindow(StrictModel):
    window_id: str
    device_group_id: str
    device_ids: List[str]
    start_time: str
    end_time: str
    data_type: str
    shape: List[int]
    features: Optional[List[float]] = None
    data: Optional[List[Any]] = None
    sampling_rate_hz: Optional[float] = None
    window_size: Optional[int] = None
    preprocess_version: str
    normalization_version: Optional[str] = None
    feature_version: str
    baseline_version: Optional[str] = None
    source: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


class StageResult(StrictModel):
    label: str
    confidence: float
    score: Optional[float] = None
    reason: Optional[str] = None


class InferenceResult(StrictModel):
    result_id: str
    window_id: str
    timestamp: str
    device_group_id: str
    stage1_label: str
    stage1_confidence: float
    stage2_label: str
    stage2_confidence: float
    final_status: str
    risk_level: str
    message: str
    model_version: str
    preprocess_version: str
    feature_version: str


class SystemStatus(StrictModel):
    server_status: str
    model_status: str
    latest_window_id: Optional[str] = None
    latest_inference_time: Optional[str] = None
    baseline_status: str
    model_version: str


class Event(StrictModel):
    event_id: str
    timestamp: str
    event_type: str
    risk_level: str
    message: str
    window_id: Optional[str] = None
    device_group_id: Optional[str] = None


class RoomState(StrictModel):
    room_id: str
    room_name: str
    device_group_id: str
    status: str
    baseline_id: Optional[str] = None
    calibration_id: Optional[str] = None
    stage1_artifact_id: str
    stage2_artifact_id: Optional[str] = None
    latest_result: Optional[Dict[str, Any]] = None
    description: Optional[str] = None
    created_at: str
    updated_at: str


class BaselineProfile(StrictModel):
    baseline_id: str
    room_id: str
    baseline_session_id: str
    duration_sec: float
    window_duration_sec: float
    preprocess_version: str
    normalization_version: str
    feature_version: str
    num_windows: int
    owner: str = "preprocessor"
    status: str
    summary: Dict[str, Any] = Field(default_factory=dict)
    created_at: str


class CompletedBaselineSession(StrictModel):
    room_id: str
    baseline_session_id: str
    baseline_id: str
    calibration_id: str
    status: str
    completed_at: str


class CalibrationWindow(StrictModel):
    calibration_session_id: str
    window_id: str
    baseline_id: str
    feature_version: str
    features: Dict[str, float]


class CalibrationSession(StrictModel):
    calibration_session_id: str
    room_id: str
    baseline_id: str
    method: str
    expected_windows: int
    window_duration_sec: float
    status: str
    created_at: str
    windows: List[CalibrationWindow] = Field(default_factory=list)


class CalibrationProfile(StrictModel):
    calibration_id: str
    room_id: str
    baseline_id: str
    stage1_artifact_id: str
    method: str
    global_threshold: float
    effective_threshold: float
    num_windows: int
    score_name: str
    score_summary: Dict[str, float]
    margin: float
    source_metadata: Dict[str, Any] = Field(default_factory=dict)
    status: str
    created_at: str


class RoomCreateRequest(StrictModel):
    room_id: str
    room_name: str
    device_group_id: str
    description: Optional[str] = None


class BaselineStartRequest(StrictModel):
    duration_sec: float
    window_duration_sec: float
    mode: str = "empty"
    requested_by: Optional[str] = None


class BaselineCommandStatusRequest(StrictModel):
    command_id: str
    status: str
    gateway_id: Optional[str] = None
    message: Optional[str] = None


class BaselineCommand(StrictModel):
    command_id: str
    room_id: str
    baseline_session_id: str
    status: str
    duration_sec: float
    window_duration_sec: float
    requested_at: str
    requested_by: Optional[str] = None
    gateway_id: Optional[str] = None
    message: Optional[str] = None
    completed_at: Optional[str] = None


class BaselineFinishRequest(StrictModel):
    baseline_session_id: str
    baseline_id: str
    duration_sec: float
    window_duration_sec: float
    num_windows: int
    preprocess_version: str
    normalization_version: str
    feature_version: str
    baseline_profile: Dict[str, Any]


class CalibrationStartRequest(StrictModel):
    baseline_id: str
    method: str = "guided_baseline_merge"
    expected_windows: int
    window_duration_sec: float


class CalibrationWindowRequest(CalibrationWindow):
    pass


class CalibrationFinishRequest(StrictModel):
    calibration_session_id: str
    method: str = "guided_baseline_merge"
    quantile: float = 0.95
    margin: float = 0.0


class RoomWindowRequest(StrictModel):
    window_id: str
    baseline_id: str
    calibration_id: str
    device_group_id: str
    window_start_time: str
    window_end_time: str
    window_duration_sec: float
    data_type: str
    feature_version: str
    features: Dict[str, float]
    motion_input: Optional[Dict[str, Any]] = None
    stage2_input: Optional[Dict[str, Any]] = None


class RoomInferenceResult(StrictModel):
    room_id: str
    window_id: str
    stage1: Dict[str, Any]
    stage2: Dict[str, Any]
    final_status: str
    timestamp: str


def model_to_dict(model: BaseModel) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()
