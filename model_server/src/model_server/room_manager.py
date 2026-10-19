from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable
from uuid import uuid4

from common.types import (
    BaselineCommand,
    BaselineCommandStatusRequest,
    BaselineFinishRequest,
    BaselineProfile,
    BaselineStartRequest,
    CalibrationProfile,
    CompletedBaselineSession,
    RoomCreateRequest,
    RoomState,
)
from storage.state_store import StateStore


class RoomManagerError(ValueError):
    pass


class RoomManager:
    def __init__(self, store: StateStore, stage1_artifact_id: str, stage2_artifact_id: str | None = None):
        self.store = store
        self.stage1_artifact_id = stage1_artifact_id
        self.stage2_artifact_id = stage2_artifact_id

    def create_room(self, request: RoomCreateRequest) -> RoomState:
        if self.store.get_room(request.room_id) is not None:
            raise RoomManagerError(f"room already exists: {request.room_id}")
        now = utc_now()
        room = RoomState(
            room_id=request.room_id,
            room_name=request.room_name,
            device_group_id=request.device_group_id,
            status="uncalibrated",
            baseline_id=None,
            calibration_id=None,
            stage1_artifact_id=self.stage1_artifact_id,
            stage2_artifact_id=self.stage2_artifact_id,
            latest_result=None,
            description=request.description,
            created_at=now,
            updated_at=now,
        )
        self.store.save_room(room)
        return room

    def get_room(self, room_id: str) -> RoomState:
        room = self.store.get_room(room_id)
        if room is None:
            raise RoomManagerError(f"room not found: {room_id}")
        return room

    def setup_status(self, room_id: str, stage2_mode: str) -> dict:
        room = self.get_room(room_id)
        calibration = self.store.get_calibration_profile(room.calibration_id) if room.calibration_id else None
        command = self.store.get_baseline_command(room_id)
        message = {
            "uncalibrated": "Room registered. Baseline calibration is required.",
            "baseline_collecting": "Baseline collection is in progress.",
            "baseline_ready": "Baseline profile registered. Calibration is required.",
            "calibration_collecting": "Calibration collection is in progress.",
            "ready": "Room is ready for inference.",
            "running": "Room is running inference.",
            "error": "Room is in error state.",
        }.get(room.status, "Room status is unknown.")
        return {
            "room_id": room.room_id,
            "status": room.status,
            "baseline_id": room.baseline_id,
            "calibration_id": room.calibration_id,
            "calibration_method": calibration.method if calibration is not None else None,
            "calibration_status": calibration.status if calibration is not None else None,
            "global_threshold": calibration.global_threshold if calibration is not None else None,
            "effective_threshold": calibration.effective_threshold if calibration is not None else None,
            "calibration_source_metadata": calibration.source_metadata if calibration is not None else None,
            "stage1_artifact_id": room.stage1_artifact_id,
            "stage2_artifact_id": room.stage2_artifact_id,
            "stage2_mode": stage2_mode,
            "baseline_command": self._baseline_command_response(command) if command is not None else None,
            "message": message,
        }

    def start_baseline(self, room_id: str, request: BaselineStartRequest) -> dict:
        if request.mode != "empty":
            raise RoomManagerError("baseline mode must be empty")
        room = self.get_room(room_id)
        active = self.store.get_baseline_command(room_id)
        if active is not None and active.status in {"pending", "collecting"}:
            return {
                "accepted": False,
                "ignored_reason": "baseline_collection_in_progress",
                **self._baseline_command_response(active),
            }

        now = datetime.now(timezone.utc)
        baseline_session_id = f"base_{room_id}_{now.strftime('%Y%m%dT%H%M%S%fZ')}"
        command = BaselineCommand(
            command_id=baseline_session_id,
            room_id=room_id,
            baseline_session_id=baseline_session_id,
            status="pending",
            duration_sec=request.duration_sec,
            window_duration_sec=request.window_duration_sec,
            requested_at=now.isoformat(),
            requested_by=request.requested_by,
        )
        self.store.save_baseline_command(command)
        updated = room.copy(update={"status": "baseline_collecting", "updated_at": utc_now()})
        self.store.save_room(updated)
        return {
            "accepted": True,
            **self._baseline_command_response(command),
            "message": "Keep the room empty during baseline collection.",
        }

    def update_baseline_command_status(
        self,
        room_id: str,
        request: BaselineCommandStatusRequest,
    ) -> dict:
        room = self.get_room(room_id)
        command = self.store.get_baseline_command(room_id)
        if command is None or command.command_id != request.command_id:
            raise RoomManagerError("baseline command not found or command_id mismatch")
        if request.status not in {"collecting", "failed"}:
            raise RoomManagerError("baseline command status must be collecting or failed")

        command = command.copy(
            update={
                "status": request.status,
                "gateway_id": request.gateway_id,
                "message": request.message,
                "completed_at": utc_now() if request.status == "failed" else None,
            }
        )
        self.store.save_baseline_command(command)
        if request.status == "failed":
            self.store.save_room(room.copy(update={"status": "error", "updated_at": utc_now()}))
        return self._baseline_command_response(command)

    def finish_baseline(
        self,
        room_id: str,
        request: BaselineFinishRequest,
        calibration_factory: Callable[[str, BaselineProfile], CalibrationProfile],
    ) -> tuple[CompletedBaselineSession, CalibrationProfile, bool]:
        self.get_room(room_id)

        def profile_factory() -> tuple[BaselineProfile, CalibrationProfile]:
            profile = self._build_baseline_profile(room_id, request)
            calibration = calibration_factory(room_id, profile)
            return profile, calibration

        return self.store.finish_baseline_session(
            room_id,
            request.baseline_session_id,
            profile_factory,
        )

    def _build_baseline_profile(self, room_id: str, request: BaselineFinishRequest) -> BaselineProfile:
        owner = str(request.baseline_profile.get("owner", "preprocessor"))
        summary = request.baseline_profile.get("summary", {})
        return BaselineProfile(
            baseline_id=request.baseline_id,
            room_id=room_id,
            baseline_session_id=request.baseline_session_id,
            duration_sec=request.duration_sec,
            window_duration_sec=request.window_duration_sec,
            preprocess_version=request.preprocess_version,
            normalization_version=request.normalization_version,
            feature_version=request.feature_version,
            num_windows=request.num_windows,
            owner=owner,
            status="ready",
            summary=summary if isinstance(summary, dict) else {"value": summary},
            created_at=utc_now(),
        )

    @staticmethod
    def _baseline_command_response(command: BaselineCommand) -> dict:
        return command.dict()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
