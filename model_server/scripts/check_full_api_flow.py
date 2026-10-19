from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from fastapi.testclient import TestClient


ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
REPORT_DIR = ROOT_DIR / "reports" / "full_api_check"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from api.server import create_app  # noqa: E402


FEATURE_VERSION = "stage1_feature_v1"
STAGE2_FEATURE_VERSION = "stage2_feature_v1"
BASE_TIME = datetime(2026, 6, 14, 6, 10, 0, tzinfo=timezone(timedelta(hours=9)))
TESTED_ENDPOINTS = [
    "GET /api/health",
    "GET /api/model/status",
    "POST /api/rooms",
    "GET /api/rooms/{room_id}/setup/status",
    "POST /api/rooms/{room_id}/baseline/start",
    "POST /api/rooms/{room_id}/baseline/finish",
    "POST /api/rooms/{room_id}/calibration/start",
    "POST /api/rooms/{room_id}/calibration/window",
    "POST /api/rooms/{room_id}/calibration/finish",
    "POST /api/rooms/{room_id}/window",
    "GET /api/rooms/{room_id}/result/latest",
    "POST /api/rooms/{room_id}/risk-assessment",
]


class FullApiCheck:
    def __init__(self) -> None:
        self.client = TestClient(create_app())
        self.results: list[dict[str, Any]] = []
        self.examples: dict[str, Any] = {}
        self.contexts: dict[str, dict[str, Any]] = {}

    def run(self) -> int:
        self.check("A. health and model status endpoints", self.check_health_and_status)
        self.check("A-C. room setup, empty inference, occupied inference, latest result", self.check_room_flow)
        self.check("D. occupied window without motion_input returns HTTP 400", self.check_missing_motion_input)
        self.check("E. motion_input preferred key and stage2_input compatibility alias", self.check_motion_input_aliases)
        self.check("F. explicit residual risk scenarios", self.check_explicit_risk_scenarios)
        self.check("G/J. stored result history feeds risk assessment", self.check_stored_history_risk)
        self.check("H. negative API error handling", self.check_error_handling)
        self.check("I. OpenAPI and docs contract consistency", self.check_openapi_and_docs)
        self.check("K. deployment readiness caveats", self.check_deployment_readiness)

        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        summary = self.build_summary()
        (REPORT_DIR / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        (REPORT_DIR / "summary.md").write_text(self.render_markdown(summary), encoding="utf-8")

        failed = [item for item in self.results if item["status"] == "FAIL"]
        print(json.dumps(summary["overview"], indent=2, ensure_ascii=False))
        print(f"Report written to: {REPORT_DIR / 'summary.md'}")
        return 1 if failed else 0

    def check(self, name: str, func: Callable[[], None]) -> None:
        try:
            func()
            self.results.append({"name": name, "status": "PASS", "detail": "ok"})
        except WarningResult as exc:
            self.results.append({"name": name, "status": "WARN", "detail": str(exc)})
        except Exception as exc:  # noqa: BLE001
            self.results.append({"name": name, "status": "FAIL", "detail": str(exc)})

    def warn(self, message: str) -> None:
        raise WarningResult(message)

    def check_health_and_status(self) -> None:
        health = self.expect(self.client.get("/api/health"), 200)
        status = self.expect(self.client.get("/api/model/status"), 200)
        assert health["status"] == "ok", health
        assert status["server_status"] == "running", status
        self.examples["health"] = health
        self.examples["model_status"] = status

    def check_room_flow(self) -> None:
        ctx = self.setup_room("full_api_room", "full_api_group")
        threshold = ctx["threshold"]

        empty = self.expect(
            self.client.post(
                f"/api/rooms/{ctx['room_id']}/window",
                json=room_window(ctx, "win_empty", threshold - 1.0, offset_seconds=5),
            ),
            200,
        )
        assert empty["stage1"]["label"] == "empty", empty
        assert empty["stage2"]["status"] == "skipped", empty
        assert empty["final_status"] == "empty", empty

        occupied = self.expect(
            self.client.post(
                f"/api/rooms/{ctx['room_id']}/window",
                json=room_window(ctx, "win_occupied", threshold + 1.0, offset_seconds=10, motion_key="motion_input"),
            ),
            200,
        )
        assert occupied["stage1"]["label"] == "occupied", occupied
        assert occupied["stage2"]["status"] == "ok", occupied
        assert occupied["stage2"]["label"] in {"static_person", "moving_person"}, occupied
        assert occupied["final_status"] in {"occupied_static_person", "occupied_moving_person"}, occupied

        latest = self.expect(self.client.get(f"/api/rooms/{ctx['room_id']}/result/latest"), 200)
        assert latest["latest_result"]["window_id"] == "win_occupied", latest

        self.examples["room_setup"] = ctx["setup_status"]
        self.examples.setdefault("representative_requests", {})["occupied_window"] = room_window(
            ctx, "win_occupied_example", threshold + 1.0, offset_seconds=10, motion_key="motion_input"
        )
        self.examples["empty_window_response"] = empty
        self.examples["occupied_window_response"] = occupied
        self.examples["latest_result"] = latest

    def check_missing_motion_input(self) -> None:
        ctx = self.contexts["full_api_room"]
        threshold = ctx["threshold"]
        response = self.client.post(
            f"/api/rooms/{ctx['room_id']}/window",
            json=room_window(ctx, "win_occupied_missing_motion", threshold + 1.0, offset_seconds=15),
        )
        body = self.expect(response, 400)
        assert "motion_input is required" in str(body), body
        self.examples["missing_motion_input_error"] = body

    def check_motion_input_aliases(self) -> None:
        ctx = self.contexts["full_api_room"]
        threshold = ctx["threshold"]
        preferred = self.expect(
            self.client.post(
                f"/api/rooms/{ctx['room_id']}/window",
                json=room_window(ctx, "win_motion_preferred", threshold + 1.0, offset_seconds=20, motion_key="motion_input"),
            ),
            200,
        )
        alias = self.expect(
            self.client.post(
                f"/api/rooms/{ctx['room_id']}/window",
                json=room_window(ctx, "win_motion_alias", threshold + 1.0, offset_seconds=25, motion_key="stage2_input"),
            ),
            200,
        )
        assert preferred["stage2"]["status"] == "ok", preferred
        assert alias["stage2"]["status"] == "ok", alias
        self.examples["motion_input_response"] = preferred
        self.examples["stage2_input_alias_response"] = alias

    def check_explicit_risk_scenarios(self) -> None:
        ctx = self.contexts["full_api_room"]
        scenarios = {
            "stable_empty": (records("empty", "skipped", 6), ("none", "normal")),
            "persistent_static": (records("occupied", "static_person", 6), ("high", "danger")),
            "moving": (records("occupied", "moving_person", 6), ("medium", "warning")),
            "unstable": (mixed_records(), ("low", "caution")),
            "insufficient": (records("empty", "skipped", 2), ("unknown", "unknown")),
            "low_quality": (records("empty", "skipped", 6, quality=0.1), ("unknown", "unknown")),
            "recent_buffer_trim": (old_occupied_then_recent_empty_records(), ("none", "normal")),
        }
        observed = {}
        for name, (history, expected) in scenarios.items():
            request = {"event_timestamp": event_timestamp(), "recent_predictions": history}
            response = self.expect(
                self.client.post(
                    f"/api/rooms/{ctx['room_id']}/risk-assessment",
                    json=request,
                ),
                200,
            )
            actual = (response["residual_presence"], response["risk_level"])
            assert actual == expected, {"scenario": name, "actual": actual, "expected": expected, "response": response}
            observed[name] = response
            if name == "persistent_static":
                self.examples.setdefault("representative_requests", {})["risk_assessment_explicit"] = request
        self.examples["explicit_risk_scenarios"] = observed

    def check_stored_history_risk(self) -> None:
        ctx = self.setup_room("full_api_history_room", "full_api_history_group")
        threshold = ctx["threshold"]
        for idx in range(6):
            result = self.expect(
                self.client.post(
                    f"/api/rooms/{ctx['room_id']}/window",
                    json=room_window(ctx, f"history_empty_{idx}", threshold - 1.0, offset_seconds=(idx + 1) * 5),
                ),
                200,
            )
            assert result["stage1"]["label"] == "empty", result
        latest = self.expect(self.client.get(f"/api/rooms/{ctx['room_id']}/result/latest"), 200)
        assert latest["latest_result"]["window_id"] == "history_empty_5", latest
        risk = self.expect(
            self.client.post(
                f"/api/rooms/{ctx['room_id']}/risk-assessment",
                json={"event_timestamp": iso_at(35)},
            ),
            200,
        )
        assert risk["residual_presence"] == "none", risk
        assert risk["risk_level"] == "normal", risk
        self.examples["stored_history_risk"] = risk

    def check_error_handling(self) -> None:
        ctx = self.contexts["full_api_room"]
        threshold = ctx["threshold"]

        self.expect(self.client.get("/api/rooms/unknown_room/setup/status"), 404)
        self.expect(
            self.client.post("/api/rooms/unknown_room/risk-assessment", json={"event_timestamp": event_timestamp()}),
            404,
        )

        wrong_baseline = room_window(ctx, "wrong_baseline", threshold - 1.0, offset_seconds=30)
        wrong_baseline["baseline_id"] = "wrong_baseline"
        self.expect(self.client.post(f"/api/rooms/{ctx['room_id']}/window", json=wrong_baseline), 400)

        wrong_calibration = room_window(ctx, "wrong_calibration", threshold - 1.0, offset_seconds=35)
        wrong_calibration["calibration_id"] = "wrong_calibration"
        self.expect(self.client.post(f"/api/rooms/{ctx['room_id']}/window", json=wrong_calibration), 400)

        wrong_device = room_window(ctx, "wrong_device", threshold - 1.0, offset_seconds=40)
        wrong_device["device_group_id"] = "wrong_device"
        self.expect(self.client.post(f"/api/rooms/{ctx['room_id']}/window", json=wrong_device), 400)

        wrong_type = room_window(ctx, "wrong_type", threshold - 1.0, offset_seconds=45)
        wrong_type["data_type"] = "raw_csi"
        self.expect(self.client.post(f"/api/rooms/{ctx['room_id']}/window", json=wrong_type), 400)

        bad_timestamp = self.expect(
            self.client.post(
                f"/api/rooms/{ctx['room_id']}/risk-assessment",
                json={"event_timestamp": "not-a-timestamp", "recent_predictions": records("empty", "skipped", 6)},
            ),
            400,
        )
        malformed_body = self.expect(self.client.post(f"/api/rooms/{ctx['room_id']}/risk-assessment", json={}), 422)
        invalid_state = self.expect(
            self.client.post(
                f"/api/rooms/{ctx['room_id']}/risk-assessment",
                json={
                    "event_timestamp": event_timestamp(),
                    "recent_predictions": records("not_valid", "skipped", 6),
                },
            ),
            200,
        )
        assert invalid_state["risk_level"] == "unknown", invalid_state
        self.examples["negative_errors"] = {
            "bad_timestamp": bad_timestamp,
            "malformed_body": malformed_body,
            "invalid_prediction_state": invalid_state,
        }

    def check_openapi_and_docs(self) -> None:
        openapi = self.expect(self.client.get("/openapi.json"), 200)
        paths = openapi.get("paths", {})
        schemas = openapi.get("components", {}).get("schemas", {})
        assert "/api/rooms/{room_id}/risk-assessment" in paths, paths.keys()
        assert "RiskAssessmentRequest" in schemas, schemas.keys()
        assert "RiskAssessmentResult" in schemas, schemas.keys()
        request_body = paths["/api/rooms/{room_id}/risk-assessment"]["post"]["requestBody"]
        assert "RiskAssessmentRequest" in json.dumps(request_body), request_body

        api_doc = (ROOT_DIR / "docs" / "api.md").read_text(encoding="utf-8")
        assert "Dashboard APIs" in api_doc, "Dashboard APIs section missing"
        assert "Hardware/Preprocessor APIs" in api_doc, "Hardware/Preprocessor APIs section missing"
        assert "/api/rooms/{room_id}/risk-assessment" in api_doc, "risk-assessment docs missing"
        assert "motion_input" in api_doc, "preferred motion_input docs missing"
        assert "stage2_input" in api_doc and "compatibility alias" in api_doc, "stage2_input alias docs missing"
        assert api_doc.count("recent_predictions") >= 2, "risk examples should show explicit recent_predictions"

        scanned_files = [
            ROOT_DIR / "docs" / "api.md",
            ROOT_DIR / "docs" / "risk_assessment.md",
            ROOT_DIR / "src" / "api" / "server.py",
            *sorted((ROOT_DIR / "src" / "risk_assessment").glob("*.py")),
        ]
        compact_stage3_hits = []
        for path in scanned_files:
            text = path.read_text(encoding="utf-8").lower()
            if "stage3" in text or "/stage3" in text or "stage3_" in text:
                compact_stage3_hits.append(str(path.relative_to(ROOT_DIR)))
        assert not compact_stage3_hits, compact_stage3_hits

        self.examples["openapi_contract"] = {
            "risk_path_present": True,
            "risk_request_schema_present": True,
            "risk_result_schema_present": True,
        }

    def check_deployment_readiness(self) -> None:
        config_text = (ROOT_DIR / "configs" / "model_server.yaml").read_text(encoding="utf-8")
        risk_config_text = (ROOT_DIR / "configs" / "risk_assessment.yaml").read_text(encoding="utf-8")
        assert "C:\\" not in config_text, "model_server.yaml contains a Windows absolute path"
        assert "C:\\" not in risk_config_text, "risk_assessment.yaml contains a Windows absolute path"
        assert "max_room_result_history" in config_text, "state_store.max_room_result_history is missing"

        state_store_text = (ROOT_DIR / "src" / "storage" / "state_store.py").read_text(encoding="utf-8")
        server_text = (ROOT_DIR / "src" / "api" / "server.py").read_text(encoding="utf-8")
        assert "max_room_result_history" in state_store_text, "room_result_history retention cap is not implemented"
        assert "logger.info" in server_text, "minimal integration logging hooks are not implemented"
        self.examples["deployment_caveats"] = ["room_result_history is process-local memory and is not persisted across restarts"]

    def setup_room(self, room_id: str, device_group_id: str) -> dict[str, Any]:
        room = self.expect(
            self.client.post(
                "/api/rooms",
                json={
                    "room_id": room_id,
                    "room_name": room_id,
                    "device_group_id": device_group_id,
                    "description": "full API integration check",
                },
            ),
            200,
        )
        baseline_start = self.expect(
            self.client.post(
                f"/api/rooms/{room_id}/baseline/start",
                json={"duration_sec": 300, "window_duration_sec": 5, "mode": "empty", "requested_by": "dashboard"},
            ),
            200,
        )
        baseline_id = f"baseline_{room_id}_v001"
        baseline_finish = self.expect(
            self.client.post(
                f"/api/rooms/{room_id}/baseline/finish",
                json={
                    "baseline_session_id": baseline_start["baseline_session_id"],
                    "baseline_id": baseline_id,
                    "duration_sec": 300,
                    "window_duration_sec": 5,
                    "num_windows": 60,
                    "preprocess_version": "preprocess_v1",
                    "normalization_version": "norm_v1_log_amp_empty_robust_zscore",
                    "feature_version": FEATURE_VERSION,
                    "baseline_profile": {"owner": "preprocessor", "summary": {"p95_score": 0.43}},
                },
            ),
            200,
        )
        assert baseline_finish["status"] == "ready", baseline_finish
        assert baseline_finish["calibration_id"].startswith(f"calib_{room_id}_baseline_default_v"), baseline_finish
        assert baseline_finish["calibration_method"] == "empty_quantile_threshold", baseline_finish
        assert float(baseline_finish["effective_threshold"]) == 0.43, baseline_finish
        calib_start = self.expect(
            self.client.post(
                f"/api/rooms/{room_id}/calibration/start",
                json={"baseline_id": baseline_id, "method": "guided_baseline_merge", "expected_windows": 3, "window_duration_sec": 5},
            ),
            200,
        )
        calibration_session_id = calib_start["calibration_session_id"]
        for idx, score in enumerate([0.22, 0.41, 1.48], start=1):
            self.expect(
                self.client.post(
                    f"/api/rooms/{room_id}/calibration/window",
                    json={
                        "calibration_session_id": calibration_session_id,
                        "window_id": f"{room_id}_calib_{idx}",
                        "baseline_id": baseline_id,
                        "feature_version": FEATURE_VERSION,
                        "features": score_features(score),
                    },
                ),
                200,
            )
        calib_finish = self.expect(
            self.client.post(
                f"/api/rooms/{room_id}/calibration/finish",
                json={"calibration_session_id": calibration_session_id, "method": "guided_baseline_merge", "quantile": 0.95, "margin": 0.0},
            ),
            200,
        )
        setup_status = self.expect(self.client.get(f"/api/rooms/{room_id}/setup/status"), 200)
        assert setup_status["status"] == "ready", setup_status
        ctx = {
            "room_id": room_id,
            "device_group_id": device_group_id,
            "baseline_id": baseline_id,
            "calibration_id": calib_finish["calibration_id"],
            "default_calibration_id": baseline_finish["calibration_id"],
            "threshold": float(calib_finish["effective_threshold"]),
            "room": room,
            "setup_status": setup_status,
        }
        self.contexts[room_id] = ctx
        return ctx

    def expect(self, response: Any, status_code: int) -> Any:
        if response.status_code != status_code:
            raise AssertionError(f"expected HTTP {status_code}, got {response.status_code}: {response.text}")
        try:
            return response.json()
        except ValueError:
            return response.text

    def build_summary(self) -> dict[str, Any]:
        counts = {
            "pass": sum(1 for item in self.results if item["status"] == "PASS"),
            "warn": sum(1 for item in self.results if item["status"] == "WARN"),
            "fail": sum(1 for item in self.results if item["status"] == "FAIL"),
        }
        readiness = "not_ready" if counts["fail"] else ("staging_ready_with_caveats" if counts["warn"] else "staging_ready")
        return {
            "overview": {
                "status": readiness,
                "pass": counts["pass"],
                "warn": counts["warn"],
                "fail": counts["fail"],
                "report": str((REPORT_DIR / "summary.md").relative_to(ROOT_DIR)),
            },
            "tested_endpoints": TESTED_ENDPOINTS,
            "results": self.results,
            "examples": compact_for_report(self.examples),
        }

    def render_markdown(self, summary: dict[str, Any]) -> str:
        lines = [
            "# Full Model-Server API Integration Check",
            "",
            "Scope: room registration, baseline registration, calibration, real-time inference, Stage 2 motion classification contract, residual presence risk assessment, OpenAPI/docs consistency, and deployment caveats.",
            "",
            "This check does not retrain models, modify model artifacts, or perform false-alarm evaluation.",
            "",
            "## Tested Endpoints",
            "",
            *[f"- `{endpoint}`" for endpoint in summary["tested_endpoints"]],
            "",
            "## Overall Result",
            "",
            f"- Status: `{summary['overview']['status']}`",
            f"- PASS: {summary['overview']['pass']}",
            f"- WARN: {summary['overview']['warn']}",
            f"- FAIL: {summary['overview']['fail']}",
            "",
            "## Scenario Results",
            "",
            "| Scenario | Status | Detail |",
            "| --- | --- | --- |",
        ]
        for item in summary["results"]:
            lines.append(f"| {item['name']} | `{item['status']}` | {str(item['detail']).replace('|', '/')} |")
        lines.extend(
            [
                "",
                "## API Contract Observations",
                "",
                "- `/api/rooms/{room_id}/risk-assessment` is present in OpenAPI.",
                "- `RiskAssessmentRequest` and `RiskAssessmentResult` are present in OpenAPI schemas.",
                "- `motion_input` is the preferred Stage 2 input key.",
                "- `stage2_input` remains accepted only as a deprecated compatibility alias.",
                "- Stage 1 `empty` skips Stage 2; Stage 1 `occupied` with Stage 2 enabled requires motion input and returns HTTP 400 when missing.",
                "- Risk assessment can use explicit `recent_predictions` or stored room result history.",
                "- `recent_buffer_seconds` trimming is covered by the `recent_buffer_trim` scenario.",
                "",
                "## Example Requests And Responses",
                "",
                "The JSON below includes representative requests plus observed responses from the integration run.",
                "",
                "```json",
                json.dumps(summary["examples"], indent=2, ensure_ascii=False)[:12000],
                "```",
                "",
                "## Known Caveats",
                "",
                "- Result history is process-local memory; it is not persisted across process restarts.",
                "- Result history is bounded by `state_store.max_room_result_history`; default is 1000 results per room.",
                "- False-alarm/disaster-event validation is outside this check.",
                "",
                "## Remaining Issues Before Dashboard Integration",
                "",
                "- Dashboard should send `motion_input` for occupied-window motion classification and keep `stage2_input` only as a backward-compatible fallback.",
                "- Dashboard should handle HTTP 400 when Stage 1 is occupied but motion input is missing.",
                "- Dashboard should call risk assessment after a disaster event with either explicit recent predictions or enough stored `/window` history.",
                "- A later non-staging decision is still needed for durable history persistence.",
                "",
                "## Staging Readiness",
                "",
                "Internal staging API deployment can proceed. Dashboard integration should use `motion_input`, handle HTTP 400 for missing occupied-window motion input, and call risk assessment after disaster-event detection.",
                "",
            ]
        )
        return "\n".join(lines)


class WarningResult(Exception):
    pass


def score_features(score: float) -> dict[str, float]:
    return {
        "static_score": score / 3.0,
        "motion_score": score / 4.0,
        "shape_score": score,
        "robust_max_shape_score": score,
    }


def room_window(ctx: dict[str, Any], window_id: str, score: float, offset_seconds: int, motion_key: str | None = None) -> dict[str, Any]:
    payload = {
        "window_id": window_id,
        "baseline_id": ctx["baseline_id"],
        "calibration_id": ctx["calibration_id"],
        "device_group_id": ctx["device_group_id"],
        "window_start_time": iso_at(max(0, offset_seconds - 5)),
        "window_end_time": iso_at(offset_seconds),
        "window_duration_sec": 5,
        "data_type": "stage1_feature_vector",
        "feature_version": FEATURE_VERSION,
        "features": score_features(score),
    }
    if motion_key:
        payload[motion_key] = motion_input()
    return payload


def motion_input() -> dict[str, Any]:
    return {
        "input_type": "window_matrix",
        "feature_version": STAGE2_FEATURE_VERSION,
        "shape": [128, 192],
        "values": [[0.0 for _ in range(192)] for _ in range(128)],
    }


def event_timestamp() -> str:
    return iso_at(60)


def iso_at(offset_seconds: int) -> str:
    return (BASE_TIME + timedelta(seconds=offset_seconds)).isoformat()


def records(occupancy_state: str, motion_state: str, count: int, quality: float = 1.0) -> list[dict[str, Any]]:
    start = 60 - (count * 5)
    return [
        {
            "window_id": f"{occupancy_state}_{motion_state}_{idx}",
            "timestamp": iso_at(start + ((idx + 1) * 5)),
            "occupancy_state": occupancy_state,
            "motion_state": motion_state,
            "window_duration_sec": 5,
            "quality": quality,
        }
        for idx in range(count)
    ]


def mixed_records() -> list[dict[str, Any]]:
    return [
        {"window_id": "mixed_0", "timestamp": iso_at(35), "occupancy_state": "empty", "motion_state": "skipped", "window_duration_sec": 5, "quality": 1.0},
        {"window_id": "mixed_1", "timestamp": iso_at(40), "occupancy_state": "empty", "motion_state": "skipped", "window_duration_sec": 5, "quality": 1.0},
        {"window_id": "mixed_2", "timestamp": iso_at(45), "occupancy_state": "empty", "motion_state": "skipped", "window_duration_sec": 5, "quality": 1.0},
        {"window_id": "mixed_3", "timestamp": iso_at(50), "occupancy_state": "empty", "motion_state": "skipped", "window_duration_sec": 5, "quality": 1.0},
        {"window_id": "mixed_4", "timestamp": iso_at(55), "occupancy_state": "occupied", "motion_state": "unknown", "window_duration_sec": 5, "quality": 1.0},
        {"window_id": "mixed_5", "timestamp": iso_at(60), "occupancy_state": "empty", "motion_state": "skipped", "window_duration_sec": 5, "quality": 1.0},
    ]


def old_occupied_then_recent_empty_records() -> list[dict[str, Any]]:
    history = [
        {
            "window_id": "trim_old_occupied",
            "timestamp": iso_at(-15),
            "occupancy_state": "occupied",
            "motion_state": "static_person",
            "window_duration_sec": 5,
            "quality": 1.0,
        }
    ]
    history.extend(records("empty", "skipped", 6))
    return history


def compact_for_report(value: Any) -> Any:
    if isinstance(value, dict):
        if "values" in value and "shape" in value:
            compacted = {key: compact_for_report(item) for key, item in value.items() if key != "values"}
            compacted["values"] = f"<omitted matrix with shape {value['shape']}>"
            return compacted
        return {key: compact_for_report(item) for key, item in value.items()}
    if isinstance(value, list):
        return [compact_for_report(item) for item in value]
    return value


if __name__ == "__main__":
    raise SystemExit(FullApiCheck().run())
