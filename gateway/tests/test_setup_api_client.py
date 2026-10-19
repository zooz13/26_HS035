import io
import json
from urllib.error import URLError

import pytest

from app.setup_api_client import SetupApiClient, SetupApiError


class Response:
    def __init__(self, status: int, value: dict | None = None) -> None:
        self.status = status
        self.body = json.dumps(value or {}).encode("utf-8")

    def __enter__(self) -> "Response":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def getcode(self) -> int:
        return self.status

    def read(self) -> bytes:
        return self.body


def test_setup_client_accepts_any_2xx_response() -> None:
    client = SetupApiClient(
        "http://127.0.0.1:8000",
        20,
        opener=lambda *args, **kwargs: Response(201, {"status": "ok"}),
    )

    assert client.create_room({"room_id": "room_101"}) == {"status": "ok"}


def test_setup_client_rejects_non_2xx_response() -> None:
    client = SetupApiClient(
        "http://127.0.0.1:8000",
        20,
        opener=lambda *args, **kwargs: Response(500),
    )

    with pytest.raises(SetupApiError, match="HTTP 500"):
        client.create_room({"room_id": "room_101"})


def test_setup_client_wraps_connection_errors() -> None:
    def fail(*args: object, **kwargs: object) -> object:
        raise URLError("offline")

    client = SetupApiClient(
        "http://127.0.0.1:8000",
        20,
        opener=fail,
    )

    with pytest.raises(SetupApiError, match="offline"):
        client.setup_status("room_101")


def test_baseline_finish_is_idempotent_for_completed_same_session() -> None:
    calls: list[str] = []

    def opener(request: object, **kwargs: object) -> Response:
        calls.append(request.get_method())
        return Response(
            200,
            {
                "status": "ready",
                "baseline_id": "baseline_room_101_v001",
                "calibration_id": (
                    "calib_room_101_baseline_default_v001"
                ),
                "calibration_method": (
                    "baseline_default_global_threshold"
                ),
                "baseline_command": {
                    "baseline_session_id": "base_001",
                    "status": "completed",
                },
            },
        )

    client = SetupApiClient(
        "http://127.0.0.1:8000",
        20,
        opener=opener,
    )

    result = client.baseline_finish(
        "room_101",
        {
            "baseline_id": "baseline_room_101_v001",
            "baseline_session_id": "base_001",
        },
    )

    assert result["idempotent"] is True
    assert result["baseline_session_id"] == "base_001"
    assert result["status"] == "ready"
    assert result["calibration_id"] == (
        "calib_room_101_baseline_default_v001"
    )
    assert calls == ["GET"]


def test_baseline_finish_posts_same_id_for_new_session() -> None:
    calls: list[str] = []

    def opener(request: object, **kwargs: object) -> Response:
        method = request.get_method()
        calls.append(method)
        if method == "GET":
            return Response(
                200,
                {
                    "status": "ready",
                    "baseline_id": "baseline_room_101_v001",
                    "calibration_id": (
                        "calib_room_101_baseline_default_v001"
                    ),
                    "baseline_command": {
                        "baseline_session_id": "base_old",
                        "status": "completed",
                    },
                },
            )
        return Response(
            200,
            {
                "status": "ready",
                "baseline_id": "baseline_room_101_v001",
                "calibration_id": (
                    "calib_room_101_baseline_default_v002"
                ),
            },
        )

    client = SetupApiClient(
        "http://127.0.0.1:8000",
        20,
        opener=opener,
    )

    result = client.baseline_finish(
        "room_101",
        {
            "baseline_id": "baseline_room_101_v001",
            "baseline_session_id": "base_new",
        },
    )

    assert result.get("idempotent") is None
    assert result["calibration_id"] == (
        "calib_room_101_baseline_default_v002"
    )
    assert calls == ["GET", "POST"]


def test_baseline_finish_posts_when_same_session_is_collecting() -> None:
    calls: list[str] = []

    def opener(request: object, **kwargs: object) -> Response:
        method = request.get_method()
        calls.append(method)
        if method == "GET":
            return Response(
                200,
                {
                    "status": "baseline_collecting",
                    "baseline_id": "baseline_room_101_v001",
                    "baseline_command": {
                        "baseline_session_id": "base_001",
                        "status": "collecting",
                    },
                },
            )
        return Response(
            200,
            {
                "status": "ready",
                "baseline_id": "baseline_room_101_v001",
                "calibration_id": (
                    "calib_room_101_baseline_default_v002"
                ),
            },
        )

    client = SetupApiClient(
        "http://127.0.0.1:8000",
        20,
        opener=opener,
    )

    client.baseline_finish(
        "room_101",
        {
            "baseline_id": "baseline_room_101_v001",
            "baseline_session_id": "base_001",
        },
    )

    assert calls == ["GET", "POST"]


def test_baseline_command_status_posts_gateway_acknowledgement() -> None:
    captured: list[dict] = []

    def opener(request: object, **kwargs: object) -> Response:
        captured.append(json.loads(request.data.decode("utf-8")))
        return Response(200, {"status": "collecting"})

    client = SetupApiClient(
        "http://127.0.0.1:8000",
        20,
        opener=opener,
    )

    result = client.baseline_command_status(
        "room_101",
        command_id="base_001",
        status="collecting",
        gateway_id="gateway-laptop-001",
    )

    assert result["status"] == "collecting"
    assert captured == [{
        "command_id": "base_001",
        "status": "collecting",
        "gateway_id": "gateway-laptop-001",
        "message": None,
    }]
