"""Realtime model-server HTTP client and dry-run payload storage."""

from __future__ import annotations

import json
import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

from app.realtime_spool import RealtimeSpoolStore


LOGGER = logging.getLogger(__name__)


class ModelServerError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RealtimeSendResult:
    success: bool
    dry_run: bool
    status_code: int | None = None
    response: dict[str, Any] | None = None
    local_payload_path: Path | None = None
    error: str | None = None


class ModelServerClient:
    def __init__(
        self,
        *,
        model_server_url: str,
        room_id: str,
        timeout_sec: float,
        send_enabled: bool,
        dryrun_dir: Path,
        spool_store: RealtimeSpoolStore,
        opener: Callable[..., Any] = urlrequest.urlopen,
        status_callback: Callable[..., None] | None = None,
    ) -> None:
        self.model_server_url = model_server_url.rstrip("/")
        self.room_id = room_id
        self.timeout_sec = timeout_sec
        self.send_enabled = send_enabled
        self.dryrun_dir = Path(dryrun_dir)
        self.spool_store = spool_store
        self.opener = opener
        self.status_callback = status_callback

    @property
    def endpoint(self) -> str:
        return (
            f"{self.model_server_url}/api/rooms/{self.room_id}/window"
        )

    def send(
        self,
        payload: dict[str, Any],
        *,
        frame_count: int,
    ) -> RealtimeSendResult:
        window_id = str(payload["window_id"])
        if not self.send_enabled:
            path = self._write_dryrun_payload(window_id, payload)
            LOGGER.info(
                "realtime payload dry run",
                extra={
                    "event": "realtime_payload_dry_run",
                    "window_id": window_id,
                    "room_id": self.room_id,
                    "frame_count": frame_count,
                    "motion_input_shape": payload["motion_input"]["shape"],
                    "feature_keys": sorted(payload["features"]),
                    "local_payload_path": str(path),
                },
            )
            self._notify(
                "realtime_payload_dry_run",
                local_payload_path=str(path),
            )
            return RealtimeSendResult(
                success=True,
                dry_run=True,
                local_payload_path=path,
            )

        try:
            status_code, response = self._post(self.endpoint, payload)
        except Exception as exc:
            self.spool_store.create(
                room_id=self.room_id,
                window_id=window_id,
                endpoint=self.endpoint,
                payload=payload,
                last_error=str(exc),
            )
            LOGGER.error(
                "realtime payload send failed",
                extra={
                    "event": "realtime_payload_send_failed",
                    "window_id": window_id,
                    "room_id": self.room_id,
                    "endpoint": self.endpoint,
                    "error": str(exc),
                },
            )
            self._notify(
                "realtime_payload_send_failed",
                error=str(exc),
                status_code=None,
            )
            return RealtimeSendResult(
                success=False,
                dry_run=False,
                error=str(exc),
            )

        LOGGER.info(
            "realtime payload sent",
            extra={
                "event": "realtime_payload_sent",
                "window_id": window_id,
                "room_id": self.room_id,
                "endpoint": self.endpoint,
                "status_code": status_code,
                "response_keys": (
                    sorted(response) if isinstance(response, dict) else []
                ),
            },
        )
        self._notify(
            "realtime_payload_sent",
            status_code=status_code,
        )
        return RealtimeSendResult(
            success=True,
            dry_run=False,
            status_code=status_code,
            response=response,
        )

    def retry_pending(self) -> list[RealtimeSendResult]:
        if not self.send_enabled:
            return []
        results: list[RealtimeSendResult] = []
        for manifest in self.spool_store.list_pending():
            try:
                payload = self.spool_store.load_payload(manifest)
                status_code, response = self._post(
                    manifest.endpoint,
                    payload,
                )
                self.spool_store.mark_sent(manifest)
                results.append(
                    RealtimeSendResult(
                        success=True,
                        dry_run=False,
                        status_code=status_code,
                        response=response,
                    )
                )
            except Exception as exc:
                self.spool_store.record_failure(manifest, str(exc))
                results.append(
                    RealtimeSendResult(
                        success=False,
                        dry_run=False,
                        error=str(exc),
                    )
                )
        return results

    def _post(
        self,
        endpoint: str,
        payload: dict[str, Any],
    ) -> tuple[int, dict[str, Any]]:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urlrequest.Request(
            endpoint,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with self.opener(request, timeout=self.timeout_sec) as response:
                status_code = int(response.getcode())
                body = response.read()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ModelServerError(
                f"POST {endpoint} failed: HTTP {exc.code}: {detail}"
            ) from exc
        except URLError as exc:
            raise ModelServerError(
                f"POST {endpoint} failed: {exc}"
            ) from exc
        if not 200 <= status_code < 300:
            raise ModelServerError(
                f"POST {endpoint} failed: HTTP {status_code}"
            )
        if not body:
            return status_code, {}
        try:
            value = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return status_code, {}
        return status_code, value if isinstance(value, dict) else {}

    def _write_dryrun_payload(
        self,
        window_id: str,
        payload: dict[str, Any],
    ) -> Path:
        self.dryrun_dir.mkdir(parents=True, exist_ok=True)
        path = self._available_path(self.dryrun_dir, window_id, ".json")
        self._atomic_write_json(path, payload)
        return path

    def _notify(self, event: str, **values: Any) -> None:
        if self.status_callback is None:
            return
        try:
            self.status_callback(event, **values)
        except Exception:
            LOGGER.warning(
                "model server status callback failed",
                extra={
                    "event": "status_callback_failed",
                    "source": "model_server_client",
                    "status_event": event,
                },
            )

    @staticmethod
    def _available_path(
        directory: Path,
        stem: str,
        suffix: str,
    ) -> Path:
        index = 0
        while True:
            candidate_stem = stem if index == 0 else f"{stem}_{index:03d}"
            candidate = directory / f"{candidate_stem}{suffix}"
            if not candidate.exists():
                return candidate
            index += 1

    @staticmethod
    def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                json.dump(
                    payload,
                    temporary,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                temporary.write("\n")
            temporary_path.replace(path)
        except Exception:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise
