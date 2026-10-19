from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient


ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from api.server import create_app  # noqa: E402
from risk_assessment.risk_assessor import RiskAssessor  # noqa: E402
from risk_assessment.types import PredictionRecord  # noqa: E402


EVENT_TS = "2026-06-14T06:01:00+09:00"


def test_stable_empty_returns_normal_none() -> None:
    result = RiskAssessor().assess("room_a", EVENT_TS, records("empty", "skipped", 12))
    assert result.residual_presence == "none"
    assert result.risk_level == "normal"
    assert result.reason == "stable_empty_before_event"


def test_persistent_static_occupied_returns_danger_high() -> None:
    result = RiskAssessor().assess("room_a", EVENT_TS, records("occupied", "static_person", 12))
    assert result.residual_presence == "high"
    assert result.risk_level == "danger"
    assert result.reason == "static_presence_persisted"


def test_moving_occupied_returns_warning_medium() -> None:
    result = RiskAssessor().assess("room_a", EVENT_TS, records("occupied", "moving_person", 12))
    assert result.residual_presence == "medium"
    assert result.risk_level == "warning"
    assert result.reason == "moving_presence_detected_but_exit_unknown"


def test_unstable_mixed_predictions_returns_caution() -> None:
    history = records("empty", "skipped", 6) + records("occupied", "unknown", 3, start_index=6) + records("empty", "skipped", 3, start_index=9)
    result = RiskAssessor().assess("room_a", EVENT_TS, history)
    assert result.risk_level == "caution"
    assert result.reason == "unstable_or_recent_presence"


def test_insufficient_input_returns_unknown() -> None:
    result = RiskAssessor().assess("room_a", EVENT_TS, records("empty", "skipped", 2))
    assert result.residual_presence == "unknown"
    assert result.risk_level == "unknown"
    assert result.reason == "insufficient_input"


def test_unavailable_motion_states_do_not_crash() -> None:
    history = []
    for idx, motion_state in enumerate(["skipped", "disabled", "unknown", "skipped", "disabled", "unknown"]):
        history.append(record(idx, "occupied", motion_state))
    result = RiskAssessor().assess("room_a", EVENT_TS, history)
    assert result.risk_level in {"caution", "unknown"}


def test_motion_state_does_not_override_stable_occupied_evidence() -> None:
    result = RiskAssessor().assess("room_a", EVENT_TS, records("occupied", "unknown", 12))
    assert result.risk_level != "normal"
    assert result.residual_presence != "none"


def test_risk_assessment_api_accepts_request_history() -> None:
    client = TestClient(create_app())
    client.post("/api/rooms", json={"room_id": "risk_room", "room_name": "Risk Room", "device_group_id": "risk_group"})
    response = client.post(
        "/api/rooms/risk_room/risk-assessment",
        json={"event_timestamp": EVENT_TS, "recent_predictions": [item.dict() for item in records("occupied", "static_person", 12)]},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["residual_presence"] == "high"
    assert payload["risk_level"] == "danger"


def test_no_new_public_stage3_naming_in_risk_docs_or_endpoint() -> None:
    risk_doc = (ROOT_DIR / "docs" / "risk_assessment.md").read_text(encoding="utf-8").lower()
    assert "stage3" not in risk_doc
    assert "/stage3" not in risk_doc
    assert "stage3_" not in risk_doc


def records(occupancy_state: str, motion_state: str, count: int, start_index: int = 0) -> list[PredictionRecord]:
    return [record(start_index + idx, occupancy_state, motion_state) for idx in range(count)]


def record(index: int, occupancy_state: str, motion_state: str) -> PredictionRecord:
    return PredictionRecord(
        window_id=f"win_{index:03d}",
        timestamp=f"2026-06-14T06:00:{index * 5:02d}+09:00",
        occupancy_state=occupancy_state,
        motion_state=motion_state,
        window_duration_sec=5,
        quality=1.0,
    )
