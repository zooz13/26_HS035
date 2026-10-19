from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError as PydanticValidationError

from common.types import (
    BaselineCommandStatusRequest,
    BaselineFinishRequest,
    BaselineStartRequest,
    CalibrationFinishRequest,
    CalibrationStartRequest,
    CalibrationWindowRequest,
    Event,
    InferenceResult,
    ProcessedWindow,
    PresignUploadRequest,
    PresignUploadResponse,
    RoomCreateRequest,
    RoomInferenceResult,
    RoomWindowRequest,
    SystemStatus,
)
from model_adapter.stage1_adapter import Stage1AdapterError
from model_adapter.stage2_adapter import Stage2Adapter, Stage2AdapterError
from model_server.calibration_manager import CalibrationManager, CalibrationManagerError, score_from_features
from model_server.config import load_model_server_config
from model_server.inference_pipeline import InferencePipeline
from model_server.input_validator import ValidationError, validate_processed_window
from model_server.room_manager import RoomManager, RoomManagerError
from model_server.s3_presigner import (
    PresignGenerationError,
    PresignValidationError,
    S3Presigner,
)
from risk_assessment.risk_assessor import RiskAssessmentError, RiskAssessor, load_policy_from_yaml
from risk_assessment.types import PredictionRecord, RiskAssessmentRequest, RiskAssessmentResult
from storage.state_store import BaselineSessionConflictError, StateStore


logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    config = load_model_server_config()
    model_config = config.get("model", {})
    state_config = config.get("state_store", {})
    s3_upload_config = config.get("s3_upload", {})
    app = FastAPI(title="CSI Model Server V1", version="0.1.0")
    pipeline = InferencePipeline(
        model_version=str(model_config.get("model_version", "dummy_model_server_v1")),
        stage1_mode=str(model_config.get("stage1_mode", "dummy")),
        stage1_artifact_path=model_config.get("stage1_artifact_path"),
        stage2_mode=str(model_config.get("stage2_mode", "dummy")),
    )
    store = StateStore(
        model_version=pipeline.model_version,
        model_status=str(pipeline.get_model_status().get("model_status", "ready")),
        max_room_result_history=int(state_config.get("max_room_result_history", 1000)),
    )
    artifact_path = Path(str(model_config.get("stage1_artifact_path", "")))
    threshold_config = _load_json(artifact_path / "stage1_threshold_config.json")
    calibration_policy = _load_json(artifact_path / "calibration_policy.json")
    metadata = _load_json(artifact_path / "metadata.json")
    stage1_artifact_id = str(metadata.get("model_id", model_config.get("model_version", "stage1_threshold_v001")))
    global_threshold = float(threshold_config.get("selected_threshold", threshold_config.get("threshold", 0.0)))
    score_name = "robust_max_shape_score"
    stage2_mode = str(model_config.get("stage2_mode", "disabled"))
    stage2_artifact_path = model_config.get("stage2_artifact_path")
    stage2_adapter = None
    stage2_artifact_id = None
    if stage2_mode == "static_moving_cnn" and stage2_artifact_path:
        try:
            stage2_adapter = Stage2Adapter(stage2_artifact_path)
            stage2_adapter.load()
            stage2_artifact_id = str(stage2_adapter.metadata.get("artifact_id", "stage2_static_moving_v001"))
        except Stage2AdapterError:
            stage2_adapter = None
            stage2_mode = "disabled"
    room_manager = RoomManager(store, stage1_artifact_id=stage1_artifact_id, stage2_artifact_id=stage2_artifact_id)
    calibration_manager = CalibrationManager(
        store,
        stage1_artifact_id=stage1_artifact_id,
        global_threshold=global_threshold,
        score_name=score_name,
        empty_quantile=float(calibration_policy.get("empty_quantile", 0.95)),
        empty_quantile_margin=float(calibration_policy.get("margin", 0.0)),
    )
    app.state.state_store = store
    s3_presigner = S3Presigner(
        bucket=str(s3_upload_config.get("bucket", "")),
        region=str(s3_upload_config.get("region", "us-west-2")),
        expires_in=int(s3_upload_config.get("presign_ttl_sec", 900)),
        allowed_prefixes=s3_upload_config.get(
            "allowed_prefixes", ["rawlog/", "preprocessed/"]
        ),
    )
    app.state.s3_presigner = s3_presigner
    risk_assessor = RiskAssessor(load_policy_from_yaml(Path(__file__).resolve().parents[2] / "configs" / "risk_assessment.yaml"))

    @app.get("/api/health")
    def health() -> Dict[str, str]:
        return {"status": "ok", "server": "running"}

    @app.post(
        "/api/s3/presign-upload",
        response_model=PresignUploadResponse,
    )
    def presign_upload(
        request: PresignUploadRequest,
    ) -> PresignUploadResponse:
        try:
            url, headers = app.state.s3_presigner.generate(
                key=request.key,
                content_type=request.content_type,
                content_encoding=request.content_encoding,
            )
        except PresignValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except PresignGenerationError as exc:
            logger.exception("S3 presigned upload URL generation failed")
            raise HTTPException(
                status_code=503,
                detail="S3 upload URL is temporarily unavailable",
            ) from exc
        return PresignUploadResponse(
            url=url,
            method="PUT",
            headers=headers,
            expires_in=app.state.s3_presigner.expires_in,
        )

    @app.get("/api/model/status", response_model=SystemStatus)
    def model_status() -> SystemStatus:
        return store.get_status()

    @app.post("/api/window", response_model=InferenceResult)
    def process_window(payload: Dict[str, Any]) -> InferenceResult:
        try:
            window = ProcessedWindow(**payload)
            validate_processed_window(window)
        except PydanticValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()) from exc
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail={"errors": exc.errors}) from exc

        try:
            result = pipeline.infer(window)
        except Stage1AdapterError as exc:
            raise HTTPException(status_code=422, detail={"errors": [str(exc)]}) from exc
        store.save_inference(window, result)
        return result

    @app.get("/api/result/latest")
    def latest_result() -> Dict[str, Any] | InferenceResult:
        result = store.get_latest_result()
        if result is None:
            return {"status": "no_result", "result": None}
        return result

    @app.get("/api/events", response_model=List[Event])
    def events() -> List[Event]:
        return store.get_events()

    @app.post("/api/rooms")
    def create_room(payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            room = room_manager.create_room(RoomCreateRequest(**payload))
        except PydanticValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()) from exc
        except RoomManagerError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        logger.info("room_created room_id=%s device_group_id=%s status=%s", room.room_id, room.device_group_id, room.status)
        return {
            "room_id": room.room_id,
            "status": room.status,
            "message": "Room registered. Baseline calibration is required.",
        }

    @app.get("/api/rooms/{room_id}/setup/status")
    def room_setup_status(room_id: str) -> Dict[str, Any]:
        try:
            return room_manager.setup_status(room_id, stage2_mode=stage2_mode)
        except RoomManagerError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/rooms/{room_id}/baseline/start")
    def start_baseline(room_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            return room_manager.start_baseline(room_id, BaselineStartRequest(**payload))
        except PydanticValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()) from exc
        except RoomManagerError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/rooms/{room_id}/baseline/command/status")
    def baseline_command_status(room_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            request = BaselineCommandStatusRequest(**payload)
            return room_manager.update_baseline_command_status(room_id, request)
        except PydanticValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()) from exc
        except RoomManagerError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/rooms/{room_id}/baseline/finish")
    def finish_baseline(room_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            completed, calibration_profile, idempotent = room_manager.finish_baseline(
                room_id,
                BaselineFinishRequest(**payload),
                calibration_manager.build_default_calibration_profile,
            )
        except PydanticValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()) from exc
        except BaselineSessionConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (RoomManagerError, CalibrationManagerError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        logger.info(
            "baseline_finished room_id=%s baseline_session_id=%s baseline_id=%s calibration_id=%s idempotent=%s",
            room_id,
            completed.baseline_session_id,
            completed.baseline_id,
            calibration_profile.calibration_id,
            idempotent,
        )
        return {
            "room_id": room_id,
            "baseline_session_id": completed.baseline_session_id,
            "baseline_id": completed.baseline_id,
            "calibration_id": calibration_profile.calibration_id,
            "calibration_method": calibration_profile.method,
            "global_threshold": calibration_profile.global_threshold,
            "effective_threshold": calibration_profile.effective_threshold,
            "score_summary": calibration_profile.score_summary,
            "source_metadata": calibration_profile.source_metadata,
            "status": completed.status,
            "idempotent": idempotent,
            "message": (
                "Previously completed baseline session result returned."
                if idempotent
                else "Baseline profile registered. Default calibration profile created. Room is ready for inference."
            ),
        }

    @app.post("/api/rooms/{room_id}/calibration/start")
    def start_calibration(room_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            session = calibration_manager.start_calibration(room_id, CalibrationStartRequest(**payload))
        except PydanticValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()) from exc
        except CalibrationManagerError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "room_id": room_id,
            "calibration_session_id": session.calibration_session_id,
            "status": session.status,
            "message": "Send empty calibration windows.",
        }

    @app.post("/api/rooms/{room_id}/calibration/window")
    def add_calibration_window(room_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            session = calibration_manager.add_calibration_window(room_id, CalibrationWindowRequest(**payload))
        except PydanticValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()) from exc
        except CalibrationManagerError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "accepted": True,
            "room_id": room_id,
            "calibration_session_id": session.calibration_session_id,
            "num_windows": len(session.windows),
            "status": session.status,
        }

    @app.post("/api/rooms/{room_id}/calibration/finish")
    def finish_calibration(room_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            profile = calibration_manager.finish_calibration(room_id, CalibrationFinishRequest(**payload))
        except PydanticValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()) from exc
        except CalibrationManagerError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        logger.info(
            "calibration_finished room_id=%s calibration_id=%s baseline_id=%s method=%s effective_threshold=%s",
            room_id,
            profile.calibration_id,
            profile.baseline_id,
            profile.method,
            profile.effective_threshold,
        )
        return {
            "room_id": room_id,
            "calibration_id": profile.calibration_id,
            "status": "ready",
            "stage1_artifact_id": profile.stage1_artifact_id,
            "baseline_id": profile.baseline_id,
            "method": profile.method,
            "global_threshold": profile.global_threshold,
            "effective_threshold": profile.effective_threshold,
            "score_summary": profile.score_summary,
            "message": "Calibration completed. Room is ready for inference.",
        }

    @app.post("/api/rooms/{room_id}/window", response_model=RoomInferenceResult)
    def process_room_window(room_id: str, payload: Dict[str, Any]) -> RoomInferenceResult:
        try:
            request = RoomWindowRequest(**payload)
            result = infer_room_window(room_id, request, store, stage1_artifact_id, stage2_mode, score_name, stage2_adapter)
        except PydanticValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        store.save_room_result(result)
        logger.info(
            "room_window_processed room_id=%s window_id=%s occupancy=%s motion_label=%s motion_status=%s final_status=%s",
            room_id,
            result.window_id,
            result.stage1.get("label"),
            result.stage2.get("label"),
            result.stage2.get("status"),
            result.final_status,
        )
        return result

    @app.get("/api/rooms/{room_id}/result/latest")
    def latest_room_result(room_id: str) -> Dict[str, Any]:
        room = store.get_room(room_id)
        if room is None:
            raise HTTPException(status_code=404, detail=f"room not found: {room_id}")
        result = store.get_room_latest_result(room_id)
        return {"room_id": room_id, "latest_result": result.dict() if result is not None else None}

    @app.post("/api/rooms/{room_id}/risk-assessment", response_model=RiskAssessmentResult)
    def assess_residual_presence(room_id: str, request: RiskAssessmentRequest) -> RiskAssessmentResult:
        try:
            room = store.get_room(room_id)
            if room is None:
                raise HTTPException(status_code=404, detail=f"room not found: {room_id}")
            predictions = request.recent_predictions
            logger.info(
                "risk_assessment_requested room_id=%s event_timestamp=%s explicit_predictions=%s prediction_count=%s",
                room_id,
                request.event_timestamp,
                predictions is not None,
                len(predictions) if predictions is not None else len(store.get_room_result_history(room_id)),
            )
            if predictions is None:
                predictions = history_to_predictions(store.get_room_result_history(room_id))
            result = risk_assessor.assess(room_id, request.event_timestamp, predictions)
            logger.info(
                "risk_assessment_result room_id=%s risk_level=%s residual_presence=%s reason=%s",
                room_id,
                result.risk_level,
                result.residual_presence,
                result.reason,
            )
            return result
        except RiskAssessmentError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    dashboard_dir = os.environ.get(
        "DASHBOARD_DIR", str(Path.home() / "dashboard")
    )
    if Path(dashboard_dir).is_dir():
        app.mount(
            "/", StaticFiles(directory=dashboard_dir, html=True), name="dashboard"
        )
        logger.info("dashboard_static_mounted dir=%s", dashboard_dir)

    return app

def infer_room_window(
    room_id: str,
    request: RoomWindowRequest,
    store: StateStore,
    stage1_artifact_id: str,
    stage2_mode: str,
    score_name: str,
    stage2_adapter: Stage2Adapter | None = None,
) -> RoomInferenceResult:
    room = store.get_room(room_id)
    if room is None:
        raise ValueError(f"room not found: {room_id}")
    if room.status not in {"ready", "running"}:
        raise ValueError("room must be ready or running before inference")
    if request.device_group_id != room.device_group_id:
        raise ValueError("device_group_id does not match the room")
    if request.baseline_id != room.baseline_id:
        raise ValueError("baseline_id does not match the room baseline")
    if request.calibration_id != room.calibration_id:
        raise ValueError("calibration_id does not match the room calibration")
    if request.data_type != "stage1_feature_vector":
        raise ValueError("room inference requires data_type=stage1_feature_vector")
    baseline = store.get_baseline_profile(request.baseline_id)
    if baseline is None:
        raise ValueError("baseline profile not found")
    if request.feature_version != baseline.feature_version:
        raise ValueError("feature_version does not match the registered baseline profile")
    calibration = store.get_calibration_profile(request.calibration_id)
    if calibration is None:
        raise ValueError("calibration profile not found")
    score = score_from_features(request.features, score_name)
    threshold = float(calibration.effective_threshold)
    label = "occupied" if score >= threshold else "empty"
    if label == "empty":
        stage2 = {"label": None, "status": "skipped", "reason": "stage1_empty"}
        final_status = "empty"
    else:
        if stage2_mode == "static_moving_cnn":
            if stage2_adapter is None:
                raise ValueError("stage2_mode is static_moving_cnn but no adapter is loaded")
            motion_input = request.motion_input if request.motion_input is not None else request.stage2_input
            if motion_input is None:
                raise ValueError("motion_input is required when occupancy is occupied and motion classification is enabled")
            try:
                prediction = stage2_adapter.predict(motion_input)
            except Stage2AdapterError as exc:
                raise ValueError(str(exc)) from exc
            stage2 = {
                "label": prediction["label"],
                "confidence": prediction["confidence"],
                "status": "ok",
                "artifact_id": prediction["artifact_id"],
                "logits": prediction["logits"],
            }
            final_status = f"occupied_{prediction['label']}"
        else:
            stage2 = {"label": "unknown", "status": "disabled"}
            final_status = "occupied_detected"
    return RoomInferenceResult(
        room_id=room_id,
        window_id=request.window_id,
        stage1={
            "label": label,
            "score": score,
            "threshold": threshold,
            "artifact_id": stage1_artifact_id,
        },
        stage2=stage2,
        final_status=final_status,
        timestamp=request.window_end_time or datetime.now(timezone.utc).isoformat(),
    )


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def history_to_predictions(history: List[RoomInferenceResult]) -> List[PredictionRecord]:
    predictions = []
    for result in history:
        occupancy_state = str(result.stage1.get("label", "unknown"))
        if occupancy_state not in {"empty", "occupied"}:
            occupancy_state = "unknown"
        motion_state = str(result.stage2.get("label") or result.stage2.get("status") or "unknown")
        if motion_state not in {"static_person", "moving_person", "skipped", "disabled", "unknown"}:
            motion_state = "unknown"
        predictions.append(
            PredictionRecord(
                window_id=result.window_id,
                timestamp=result.timestamp,
                occupancy_state=occupancy_state,
                motion_state=motion_state,
                occupancy_confidence=None,
                motion_confidence=result.stage2.get("confidence"),
                window_duration_sec=5.0,
            )
        )
    return predictions


app = create_app()
