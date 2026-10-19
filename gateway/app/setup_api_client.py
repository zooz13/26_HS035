"""Fail-fast HTTP client for ordered room setup operations."""

from __future__ import annotations

import json
import logging
from typing import Any, Callable
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError


LOGGER = logging.getLogger(__name__)


class SetupApiError(RuntimeError):
    pass


class SetupApiClient:
    def __init__(
        self,
        server_url: str,
        timeout_sec: float,
        *,
        opener: Callable[..., Any] = urlrequest.urlopen,
    ) -> None:
        self.server_url = server_url.rstrip("/")
        self.timeout_sec = timeout_sec
        self.opener = opener

    def create_room(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.post("/api/rooms", payload)

    def setup_status(self, room_id: str) -> dict[str, Any]:
        return self.get(f"/api/rooms/{room_id}/setup/status")

    def baseline_start(
        self,
        room_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return self.post(f"/api/rooms/{room_id}/baseline/start", payload)

    def baseline_finish(
        self,
        room_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        baseline_id = payload.get("baseline_id")
        baseline_session_id = payload.get("baseline_session_id")
        try:
            status = self.setup_status(room_id)
            command = status.get("baseline_command")
            if (
                isinstance(command, dict)
                and status.get("baseline_id") == baseline_id
                and status.get("status")
                in {"baseline_ready", "calibration_collecting", "ready", "running"}
                and command.get("baseline_session_id")
                == baseline_session_id
                and command.get("status") == "completed"
            ):
                LOGGER.info(
                    "baseline finish skipped for completed session",
                    extra={
                        "event": "baseline_finish_skipped_exact_retry",
                        "room_id": room_id,
                        "baseline_id": baseline_id,
                        "baseline_session_id": baseline_session_id,
                        "server_status": status.get("status"),
                        "command_status": command.get("status"),
                    },
                )
                return {
                    "room_id": room_id,
                    "baseline_id": baseline_id,
                    "baseline_session_id": baseline_session_id,
                    "calibration_id": status.get("calibration_id"),
                    "calibration_method": status.get(
                        "calibration_method"
                    ),
                    "status": status.get("status"),
                    "idempotent": True,
                }
        except SetupApiError:
            pass
        LOGGER.info(
            "baseline finish dispatching",
            extra={
                "event": "baseline_finish_dispatching",
                "room_id": room_id,
                "baseline_id": baseline_id,
                "baseline_session_id": baseline_session_id,
            },
        )
        response = self.post(
            f"/api/rooms/{room_id}/baseline/finish",
            payload,
        )
        LOGGER.info(
            "baseline finish succeeded",
            extra={
                "event": "baseline_finish_succeeded",
                "room_id": room_id,
                "baseline_id": baseline_id,
                "baseline_session_id": baseline_session_id,
                "calibration_id": response.get("calibration_id"),
                "server_status": response.get("status"),
            },
        )
        return response

    def baseline_command_status(
        self,
        room_id: str,
        *,
        command_id: str,
        status: str,
        gateway_id: str,
        message: str | None = None,
    ) -> dict[str, Any]:
        return self.post(
            f"/api/rooms/{room_id}/baseline/command/status",
            {
                "command_id": command_id,
                "status": status,
                "gateway_id": gateway_id,
                "message": message,
            },
        )

    def calibration_start(
        self,
        room_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return self.post(
            f"/api/rooms/{room_id}/calibration/start",
            payload,
        )

    def calibration_window(
        self,
        room_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return self.post(
            f"/api/rooms/{room_id}/calibration/window",
            payload,
        )

    def calibration_finish(
        self,
        room_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            status = self.setup_status(room_id)
            if (
                status.get("status") in {"ready", "running"}
                and status.get("calibration_id")
            ):
                return {
                    "room_id": room_id,
                    "calibration_id": status["calibration_id"],
                    "status": status["status"],
                    "idempotent": True,
                }
        except SetupApiError:
            pass
        return self.post(
            f"/api/rooms/{room_id}/calibration/finish",
            payload,
        )

    def send_finish_envelope(
        self,
        envelope: dict[str, Any],
    ) -> dict[str, Any]:
        room_id = str(envelope["room_id"])
        payload = dict(envelope["payload"])
        operation = envelope["operation"]
        if operation == "baseline_finish":
            return self.baseline_finish(room_id, payload)
        if operation == "calibration_finish":
            return self.calibration_finish(room_id, payload)
        raise SetupApiError(f"unsupported finish operation: {operation}")

    def get(self, path: str) -> dict[str, Any]:
        request = urlrequest.Request(
            self.server_url + path,
            method="GET",
        )
        return self._open(request)

    def post(
        self,
        path: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        request = urlrequest.Request(
            self.server_url + path,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        return self._open(request)

    def _open(self, request: urlrequest.Request) -> dict[str, Any]:
        try:
            with self.opener(request, timeout=self.timeout_sec) as response:
                status_code = int(response.getcode())
                body = response.read()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise SetupApiError(
                f"{request.full_url} failed: HTTP {exc.code}: {detail}"
            ) from exc
        except (URLError, OSError) as exc:
            raise SetupApiError(
                f"{request.full_url} failed: {exc}"
            ) from exc
        if not 200 <= status_code < 300:
            raise SetupApiError(
                f"{request.full_url} failed: HTTP {status_code}"
            )
        if not body:
            return {}
        try:
            value = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}
