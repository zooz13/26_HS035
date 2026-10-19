from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RiskPolicy:
    recent_buffer_seconds: float = 60.0
    stable_empty_ratio: float = 0.9
    occupied_ratio_threshold: float = 0.7
    static_ratio_threshold: float = 0.6
    moving_ratio_threshold: float = 0.5
    static_persistence_seconds: float = 30.0
    recent_occupied_seconds: float = 10.0
    minimum_window_count: int = 6
    default_window_duration_sec: float = 5.0
    minimum_quality: float = 0.5


VALID_OCCUPANCY_STATES = {"empty", "occupied", "unknown"}
VALID_MOTION_STATES = {"static_person", "moving_person", "skipped", "disabled", "unknown"}
