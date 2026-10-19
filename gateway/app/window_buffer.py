"""UTC-aligned buffering for inference and rawlog payloads."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import floor, isfinite
from typing import Any

from app.models import CanonicalCsiFrame, ParseErrorRecord, PayloadType


@dataclass(frozen=True, slots=True)
class CompletedWindow:
    """An immutable window ready for JSONL and metadata generation."""

    payload_type: PayloadType
    window_start: float
    window_end: float
    window_sec: int
    records: tuple[dict[str, Any], ...]
    frame_count: int
    parse_error_count: int
    rssi_min: int | None
    rssi_max: int | None
    rssi_avg: float | None
    noise_floor_avg: float | None

    @property
    def estimated_fps(self) -> float:
        return self.frame_count / self.window_sec


@dataclass(slots=True)
class _WindowState:
    payload_type: PayloadType
    window_start: float
    window_end: float
    window_sec: int
    records: list[dict[str, Any]] = field(default_factory=list)
    frame_count: int = 0
    parse_error_count: int = 0
    rssi_values: list[int] = field(default_factory=list)
    noise_floor_values: list[int] = field(default_factory=list)

    def add_frame(self, frame: CanonicalCsiFrame) -> None:
        self.records.append(
            frame.to_dict(
                include_raw_line=self.payload_type is PayloadType.RAWLOG_1M
            )
        )
        self.frame_count += 1
        self.rssi_values.append(frame.rssi)
        self.noise_floor_values.append(frame.noise_floor)

    def add_parse_error(self, error_record: ParseErrorRecord) -> None:
        self.parse_error_count += 1
        if self.payload_type is PayloadType.RAWLOG_1M:
            self.records.append(error_record.to_dict())

    def complete(self) -> CompletedWindow:
        return CompletedWindow(
            payload_type=self.payload_type,
            window_start=self.window_start,
            window_end=self.window_end,
            window_sec=self.window_sec,
            records=tuple(self.records),
            frame_count=self.frame_count,
            parse_error_count=self.parse_error_count,
            rssi_min=min(self.rssi_values) if self.rssi_values else None,
            rssi_max=max(self.rssi_values) if self.rssi_values else None,
            rssi_avg=(
                sum(self.rssi_values) / len(self.rssi_values)
                if self.rssi_values
                else None
            ),
            noise_floor_avg=(
                sum(self.noise_floor_values) / len(self.noise_floor_values)
                if self.noise_floor_values
                else None
            ),
        )


class WindowBuffer:
    """Build 5-second realtime and 1-minute rawlog windows."""

    def __init__(
        self,
        realtime_window_sec: int = 5,
        raw_log_window_sec: int = 60,
    ) -> None:
        if realtime_window_sec <= 0:
            raise ValueError("realtime_window_sec must be greater than zero")
        if raw_log_window_sec <= 0:
            raise ValueError("raw_log_window_sec must be greater than zero")

        self.realtime_window_sec = realtime_window_sec
        self.raw_log_window_sec = raw_log_window_sec
        self._states: dict[tuple[PayloadType, float], _WindowState] = {}

    def add_frame(self, frame: CanonicalCsiFrame) -> list[CompletedWindow]:
        """Close expired windows, then add a frame to both active windows."""

        completed = self.advance_time(frame.gateway_ts)
        self._state_for(
            PayloadType.REALTIME_WINDOW,
            frame.gateway_ts,
            self.realtime_window_sec,
        ).add_frame(frame)
        self._state_for(
            PayloadType.RAWLOG_1M,
            frame.gateway_ts,
            self.raw_log_window_sec,
        ).add_frame(frame)
        return completed

    def add_parse_error(
        self,
        error_record: ParseErrorRecord,
    ) -> list[CompletedWindow]:
        """Retain parse errors only in the rawlog window."""

        completed = self.advance_time(error_record.gateway_ts)
        self._state_for(
            PayloadType.RAWLOG_1M,
            error_record.gateway_ts,
            self.raw_log_window_sec,
        ).add_parse_error(error_record)
        return completed

    def advance_time(self, gateway_ts: float) -> list[CompletedWindow]:
        """Close windows whose exclusive end is at or before `gateway_ts`."""

        timestamp = self._validate_timestamp(gateway_ts)
        completed: list[CompletedWindow] = []

        expired_keys = [
            key
            for key, state in self._states.items()
            if state.window_end <= timestamp
        ]
        for key in expired_keys:
            state = self._states.pop(key)
            if self._should_emit(state):
                completed.append(state.complete())

        return self._sort_completed(completed)

    def flush(self) -> list[CompletedWindow]:
        """Return all non-empty buffered windows and clear the buffer."""

        completed = [
            state.complete()
            for state in self._states.values()
            if self._should_emit(state)
        ]
        self._states.clear()
        return self._sort_completed(completed)

    def _state_for(
        self,
        payload_type: PayloadType,
        gateway_ts: float,
        window_sec: int,
    ) -> _WindowState:
        timestamp = self._validate_timestamp(gateway_ts)
        window_start = floor(timestamp / window_sec) * window_sec
        key = (payload_type, float(window_start))

        if key not in self._states:
            self._states[key] = _WindowState(
                payload_type=payload_type,
                window_start=float(window_start),
                window_end=float(window_start + window_sec),
                window_sec=window_sec,
            )
        return self._states[key]

    @staticmethod
    def _validate_timestamp(gateway_ts: float) -> float:
        if not isinstance(gateway_ts, (int, float)) or not isfinite(gateway_ts):
            raise ValueError("gateway_ts must be a finite Unix epoch number")
        return float(gateway_ts)

    @staticmethod
    def _should_emit(state: _WindowState) -> bool:
        if state.payload_type is PayloadType.REALTIME_WINDOW:
            return state.frame_count > 0
        return state.frame_count > 0 or state.parse_error_count > 0

    @staticmethod
    def _sort_completed(
        completed: list[CompletedWindow],
    ) -> list[CompletedWindow]:
        return sorted(
            completed,
            key=lambda window: (
                window.window_end,
                window.payload_type.value,
            ),
        )
