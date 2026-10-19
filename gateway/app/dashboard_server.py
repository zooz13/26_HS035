"""Local dashboard HTTP server with a narrow firmware-control API."""

from __future__ import annotations

import json
import logging
import threading
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from app.firmware_control import FirmwareControlError, FirmwareController


LOGGER = logging.getLogger(__name__)
MAX_REQUEST_BYTES = 4096


class DashboardHttpServer:
    """Serve dashboard assets and local-only firmware command endpoints."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        dashboard_dir: Path,
        firmware: FirmwareController,
        status_snapshot: Callable[[], dict[str, Any]] | None = None,
        server_factory: Callable[..., ThreadingHTTPServer] = ThreadingHTTPServer,
    ) -> None:
        self.host = host
        self.port = port
        self.dashboard_dir = Path(dashboard_dir).resolve()
        self.firmware = firmware
        self.status_snapshot = status_snapshot
        self.server_factory = server_factory
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def address(self) -> tuple[str, int] | None:
        if self._server is None:
            return None
        host, port = self._server.server_address[:2]
        return str(host), int(port)

    def start(self) -> None:
        if self._server is not None:
            return
        handler = partial(
            _DashboardRequestHandler,
            directory=str(self.dashboard_dir),
            firmware=self.firmware,
            status_snapshot=self.status_snapshot,
        )
        self._server = self.server_factory((self.host, self.port), handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="dashboard-http",
            daemon=True,
        )
        self._thread.start()
        LOGGER.info(
            "dashboard HTTP server started",
            extra={
                "event": "dashboard_http_started",
                "host": self.address[0] if self.address else self.host,
                "port": self.address[1] if self.address else self.port,
            },
        )

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._server = None
        self._thread = None


class _DashboardRequestHandler(SimpleHTTPRequestHandler):
    def __init__(
        self,
        *args: Any,
        firmware: FirmwareController,
        status_snapshot: Callable[[], dict[str, Any]] | None,
        directory: str,
        **kwargs: Any,
    ) -> None:
        self.firmware = firmware
        self.status_snapshot = status_snapshot
        super().__init__(*args, directory=directory, **kwargs)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        if urlsplit(self.path).path == "/status.json" and self.status_snapshot:
            self._json_response(HTTPStatus.OK, self.status_snapshot())
            return
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        path = urlsplit(self.path).path
        try:
            if path == "/api/firmware/settings":
                body = self._read_json()
                result = self.firmware.set_settings(
                    body.get("rate_hz"), body.get("tx_power_dbm")
                )
            elif path == "/api/firmware/refresh":
                self._require_empty_or_object()
                result = self.firmware.request_settings()
            elif path == "/api/firmware/reset":
                self._require_empty_or_object()
                result = self.firmware.reset_settings()
            else:
                self._json_response(
                    HTTPStatus.NOT_FOUND,
                    {"error": "endpoint not found"},
                )
                return
        except (
            FirmwareControlError,
            ValueError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            self._json_response(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        self._json_response(HTTPStatus.ACCEPTED, result)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        LOGGER.debug(
            "dashboard HTTP request",
            extra={"event": "dashboard_http_request", "detail": format % args},
        )

    def _read_json(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise ValueError("Content-Length is required")
        length = int(raw_length)
        if length <= 0 or length > MAX_REQUEST_BYTES:
            raise ValueError("request body size is invalid")
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(body, dict):
            raise ValueError("request body must be a JSON object")
        return body

    def _require_empty_or_object(self) -> None:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None or int(raw_length) == 0:
            return
        self._read_json()

    def _json_response(self, status: HTTPStatus, value: dict[str, Any]) -> None:
        encoded = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)
