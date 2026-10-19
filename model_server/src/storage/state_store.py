from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock
from typing import Callable, Dict, List, Optional, Tuple
from uuid import uuid4

from common.types import (
    BaselineCommand,
    BaselineProfile,
    CalibrationProfile,
    CalibrationSession,
    CompletedBaselineSession,
    Event,
    InferenceResult,
    ProcessedWindow,
    RoomInferenceResult,
    RoomState,
    SystemStatus,
)
from model_server.result_aggregator import DEFAULT_MODEL_VERSION


class BaselineSessionConflictError(ValueError):
    pass


class StateStore:
    def __init__(
        self,
        model_version: str = DEFAULT_MODEL_VERSION,
        model_status: str = "dummy_ready",
        max_room_result_history: int = 1000,
    ):
        self._lock = RLock()
        self.max_room_result_history = max(1, int(max_room_result_history))
        self.latest_window: Optional[ProcessedWindow] = None
        self.latest_result: Optional[InferenceResult] = None
        self.room_states: Dict[str, RoomState] = {}
        self.baseline_profiles: Dict[str, BaselineProfile] = {}
        self.baseline_commands: Dict[str, BaselineCommand] = {}
        self.completed_baseline_sessions: Dict[Tuple[str, str], CompletedBaselineSession] = {}
        self.calibration_sessions: Dict[str, CalibrationSession] = {}
        self.calibration_profiles: Dict[str, CalibrationProfile] = {}
        self.room_latest_results: Dict[str, RoomInferenceResult] = {}
        self.room_result_history: Dict[str, List[RoomInferenceResult]] = {}
        self.events: List[Event] = []
        self.system_status = SystemStatus(
            server_status="running",
            model_status=model_status,
            latest_window_id=None,
            latest_inference_time=None,
            baseline_status="not_loaded",
            model_version=model_version,
        )

    def save_inference(self, window: ProcessedWindow, result: InferenceResult) -> None:
        with self._lock:
            self.latest_window = window
            self.latest_result = result
            self.system_status.latest_window_id = window.window_id
            self.system_status.latest_inference_time = result.timestamp
            self.events.append(
                Event(
                    event_id=f"event_{uuid4().hex}",
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    event_type="inference_completed",
                    risk_level=result.risk_level,
                    message=result.message,
                    window_id=window.window_id,
                    device_group_id=window.device_group_id,
                )
            )

    def get_latest_result(self) -> Optional[InferenceResult]:
        with self._lock:
            return self.latest_result

    def get_status(self) -> SystemStatus:
        with self._lock:
            return self.system_status.copy()

    def get_events(self) -> List[Event]:
        with self._lock:
            return list(self.events)

    def save_room(self, room: RoomState) -> None:
        with self._lock:
            self.room_states[room.room_id] = room

    def get_room(self, room_id: str) -> Optional[RoomState]:
        with self._lock:
            return self.room_states.get(room_id)

    def save_baseline_profile(self, profile: BaselineProfile) -> None:
        with self._lock:
            self.baseline_profiles[profile.baseline_id] = profile
            self.system_status.baseline_status = "loaded"

    def get_baseline_profile(self, baseline_id: str) -> Optional[BaselineProfile]:
        with self._lock:
            return self.baseline_profiles.get(baseline_id)

    def save_baseline_command(self, command: BaselineCommand) -> None:
        with self._lock:
            self.baseline_commands[command.room_id] = command

    def get_baseline_command(self, room_id: str) -> Optional[BaselineCommand]:
        with self._lock:
            return self.baseline_commands.get(room_id)

    def get_completed_baseline_session(
        self, room_id: str, baseline_session_id: str
    ) -> Optional[CompletedBaselineSession]:
        with self._lock:
            return self.completed_baseline_sessions.get((room_id, baseline_session_id))

    def finish_baseline_session(
        self,
        room_id: str,
        baseline_session_id: str,
        profile_factory: Callable[[], tuple[BaselineProfile, CalibrationProfile]],
    ) -> tuple[CompletedBaselineSession, CalibrationProfile, bool]:
        """Atomically commit baseline finish or return its previously committed result."""
        with self._lock:
            session_key = (room_id, baseline_session_id)
            command = self.baseline_commands.get(room_id)
            if command is None:
                raise BaselineSessionConflictError(
                    "no active baseline command exists for the room"
                )
            if command.baseline_session_id != baseline_session_id:
                raise BaselineSessionConflictError(
                    "baseline_session_id does not match the current baseline command"
                )

            completed = self.completed_baseline_sessions.get(session_key)
            if completed is not None:
                calibration = self.calibration_profiles.get(completed.calibration_id)
                if calibration is None:
                    raise RuntimeError(
                        f"completed baseline session calibration is missing: {completed.calibration_id}"
                    )
                return completed, calibration, True

            if command.status not in {"pending", "collecting"}:
                raise BaselineSessionConflictError(
                    f"baseline command is not active: {command.status}"
                )

            baseline, calibration = profile_factory()
            if baseline.room_id != room_id or baseline.baseline_session_id != baseline_session_id:
                raise ValueError("baseline profile does not match the finishing session")
            if calibration.room_id != room_id or calibration.baseline_id != baseline.baseline_id:
                raise ValueError("default calibration does not match the baseline profile")

            room = self.room_states.get(room_id)
            if room is None:
                raise ValueError(f"room not found: {room_id}")

            completed_at = datetime.now(timezone.utc).isoformat()
            completed = CompletedBaselineSession(
                room_id=room_id,
                baseline_session_id=baseline_session_id,
                baseline_id=baseline.baseline_id,
                calibration_id=calibration.calibration_id,
                status="ready",
                completed_at=completed_at,
            )

            self.baseline_profiles[baseline.baseline_id] = baseline
            self.calibration_profiles[calibration.calibration_id] = calibration
            self.room_states[room_id] = room.copy(
                update={
                    "status": "ready",
                    "baseline_id": baseline.baseline_id,
                    "calibration_id": calibration.calibration_id,
                    "updated_at": completed_at,
                }
            )
            self.baseline_commands[room_id] = command.copy(
                update={"status": "completed", "completed_at": completed_at, "message": None}
            )
            self.completed_baseline_sessions[session_key] = completed
            self.system_status.baseline_status = "loaded"
            return completed, calibration, False

    def save_calibration_session(self, session: CalibrationSession) -> None:
        with self._lock:
            self.calibration_sessions[session.calibration_session_id] = session

    def get_calibration_session(self, calibration_session_id: str) -> Optional[CalibrationSession]:
        with self._lock:
            return self.calibration_sessions.get(calibration_session_id)

    def save_calibration_profile(self, profile: CalibrationProfile) -> None:
        with self._lock:
            self.calibration_profiles[profile.calibration_id] = profile

    def get_calibration_profile(self, calibration_id: str) -> Optional[CalibrationProfile]:
        with self._lock:
            return self.calibration_profiles.get(calibration_id)

    def save_room_result(self, result: RoomInferenceResult) -> None:
        with self._lock:
            self.room_latest_results[result.room_id] = result
            history = self.room_result_history.setdefault(result.room_id, [])
            history.append(result)
            if len(history) > self.max_room_result_history:
                del history[: len(history) - self.max_room_result_history]
            self.system_status.latest_window_id = result.window_id
            self.system_status.latest_inference_time = result.timestamp
            room = self.room_states.get(result.room_id)
            if room is not None:
                room.latest_result = result.dict()
                room.status = "running"
                room.updated_at = result.timestamp
                self.room_states[result.room_id] = room

    def get_room_latest_result(self, room_id: str) -> Optional[RoomInferenceResult]:
        with self._lock:
            return self.room_latest_results.get(room_id)

    def get_room_result_history(self, room_id: str) -> List[RoomInferenceResult]:
        with self._lock:
            return list(self.room_result_history.get(room_id, []))
