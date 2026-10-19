from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Iterable, List

from risk_assessment.rules import RiskPolicy, VALID_MOTION_STATES, VALID_OCCUPANCY_STATES
from risk_assessment.types import MotionSummary, OccupancySummary, PredictionRecord, RiskAssessmentResult


class RiskAssessmentError(ValueError):
    pass


class RiskAssessor:
    def __init__(self, policy: RiskPolicy | None = None):
        self.policy = policy or RiskPolicy()

    def assess(self, room_id: str, event_timestamp: str, predictions: Iterable[PredictionRecord]) -> RiskAssessmentResult:
        records = list(predictions)
        limitations: List[str] = []
        malformed = validate_records(records)
        if malformed:
            return unknown_result(room_id, event_timestamp, "insufficient_input", malformed)

        records = trim_recent(records, event_timestamp, self.policy.recent_buffer_seconds)
        if len(records) < self.policy.minimum_window_count:
            return unknown_result(room_id, event_timestamp, "insufficient_input", ["prediction history shorter than minimum_window_count"])
        low_quality = [record for record in records if record.quality is not None and record.quality < self.policy.minimum_quality]
        if low_quality:
            return unknown_result(room_id, event_timestamp, "insufficient_input", ["low quality prediction records present"])

        total = len(records)
        empty_count = sum(record.occupancy_state == "empty" for record in records)
        occupied_count = sum(record.occupancy_state == "occupied" for record in records)
        unknown_count = sum(record.occupancy_state == "unknown" for record in records)
        if unknown_count:
            limitations.append("unknown occupancy states are present")
        empty_ratio = empty_count / total
        occupied_ratio = occupied_count / total
        recent_occupied = has_recent_occupied(records, event_timestamp, self.policy)

        occupied_records = [record for record in records if record.occupancy_state == "occupied"]
        static_count = sum(record.motion_state == "static_person" for record in occupied_records)
        moving_count = sum(record.motion_state == "moving_person" for record in occupied_records)
        motion_denominator = max(1, len(occupied_records))
        static_ratio = static_count / motion_denominator
        moving_ratio = moving_count / motion_denominator
        static_persistence = trailing_static_seconds(records, self.policy.default_window_duration_sec)
        motion_unknown = [record for record in occupied_records if record.motion_state in {"skipped", "disabled", "unknown"}]
        if motion_unknown:
            limitations.append("some occupied records have unavailable motion state")

        occupancy_summary = OccupancySummary(empty_ratio=empty_ratio, occupied_ratio=occupied_ratio, recent_occupied=recent_occupied)
        motion_summary = MotionSummary(
            static_ratio=static_ratio,
            moving_ratio=moving_ratio,
            static_persistence_seconds=static_persistence,
        )

        if empty_ratio >= self.policy.stable_empty_ratio and not recent_occupied:
            return result(room_id, event_timestamp, "none", "normal", occupancy_summary, motion_summary, 0.95, "stable_empty_before_event", limitations)

        if (
            occupied_ratio >= self.policy.occupied_ratio_threshold
            and static_ratio >= self.policy.static_ratio_threshold
            and static_persistence >= self.policy.static_persistence_seconds
        ):
            return result(room_id, event_timestamp, "high", "danger", occupancy_summary, motion_summary, 0.9, "static_presence_persisted", limitations)

        if occupied_ratio >= self.policy.occupied_ratio_threshold and moving_ratio >= self.policy.moving_ratio_threshold:
            return result(
                room_id,
                event_timestamp,
                "medium",
                "warning",
                occupancy_summary,
                motion_summary,
                0.82,
                "moving_presence_detected_but_exit_unknown",
                limitations + ["moving_person does not confirm evacuation"],
            )

        if recent_occupied or occupied_ratio > 0.0:
            residual = "medium" if occupied_ratio >= 0.35 else "low"
            confidence = 0.7 if residual == "medium" else 0.62
            return result(room_id, event_timestamp, residual, "caution", occupancy_summary, motion_summary, confidence, "unstable_or_recent_presence", limitations)

        return unknown_result(room_id, event_timestamp, "insufficient_input", limitations + ["no decisive rule matched"])


def validate_records(records: list[PredictionRecord]) -> list[str]:
    if not records:
        return ["prediction history is missing"]
    problems = []
    for idx, record in enumerate(records):
        if record.occupancy_state not in VALID_OCCUPANCY_STATES:
            problems.append(f"invalid occupancy_state at index {idx}")
        if record.motion_state not in VALID_MOTION_STATES:
            problems.append(f"invalid motion_state at index {idx}")
        if not record.timestamp:
            problems.append(f"missing timestamp at index {idx}")
        if record.window_duration_sec <= 0:
            problems.append(f"invalid window_duration_sec at index {idx}")
    return problems


def trim_recent(records: list[PredictionRecord], event_timestamp: str, buffer_seconds: float) -> list[PredictionRecord]:
    event_dt = parse_timestamp(event_timestamp)
    with_age = []
    for record in records:
        age = (event_dt - parse_timestamp(record.timestamp)).total_seconds()
        if age >= 0 and age <= buffer_seconds:
            with_age.append((age, record))
    with_age.sort(key=lambda item: item[0], reverse=True)
    return [record for _, record in with_age]


def has_recent_occupied(records: list[PredictionRecord], event_timestamp: str, policy: RiskPolicy) -> bool:
    event_dt = parse_timestamp(event_timestamp)
    fallback_count = max(1, int(policy.recent_occupied_seconds / policy.default_window_duration_sec))
    recent_by_time = []
    for record in records:
        age = (event_dt - parse_timestamp(record.timestamp)).total_seconds()
        if age >= 0 and age <= policy.recent_occupied_seconds:
            recent_by_time.append(record)
    recent = recent_by_time if recent_by_time else records[-fallback_count:]
    return any(record.occupancy_state == "occupied" for record in recent)


def trailing_static_seconds(records: list[PredictionRecord], default_window_duration_sec: float) -> float:
    seconds = 0.0
    for record in reversed(records):
        if record.occupancy_state == "occupied" and record.motion_state == "static_person":
            seconds += record.window_duration_sec or default_window_duration_sec
        else:
            break
    return seconds


def parse_timestamp(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RiskAssessmentError(f"invalid timestamp: {value}") from exc


def result(
    room_id: str,
    event_timestamp: str,
    residual_presence: str,
    risk_level: str,
    occupancy_summary: OccupancySummary,
    motion_summary: MotionSummary,
    confidence: float,
    reason: str,
    limitations: list[str],
) -> RiskAssessmentResult:
    return RiskAssessmentResult(
        room_id=room_id,
        event_timestamp=event_timestamp,
        residual_presence=residual_presence,
        risk_level=risk_level,
        occupancy_summary=occupancy_summary,
        motion_summary=motion_summary,
        confidence=confidence,
        reason=reason,
        limitations=limitations,
    )


def unknown_result(room_id: str, event_timestamp: str, reason: str, limitations: list[str]) -> RiskAssessmentResult:
    return result(
        room_id,
        event_timestamp,
        "unknown",
        "unknown",
        OccupancySummary(empty_ratio=0.0, occupied_ratio=0.0, recent_occupied=False),
        MotionSummary(static_ratio=0.0, moving_ratio=0.0, static_persistence_seconds=0.0),
        0.0,
        reason,
        limitations,
    )


def load_policy_from_yaml(path: str | Path) -> RiskPolicy:
    path = Path(path)
    if not path.exists():
        return RiskPolicy()
    values = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = coerce_scalar(value.strip())
    allowed = RiskPolicy.__dataclass_fields__.keys()
    return RiskPolicy(**{key: value for key, value in values.items() if key in allowed})


def coerce_scalar(value: str):
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value.strip("\"'")
