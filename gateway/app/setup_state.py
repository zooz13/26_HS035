"""Persistent room setup state with periodic hot reload."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


LOGGER = logging.getLogger(__name__)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(
                value,
                temporary,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        temporary_path.replace(destination)
    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise


@dataclass(frozen=True, slots=True)
class SetupState:
    room_id: str
    device_group_id: str
    baseline_id: str | None
    calibration_id: str | None
    baseline_state_json: str
    server_url: str
    feature_version: str
    preprocessing_type: str
    setup_status: str
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SetupState":
        return cls(
            room_id=str(value["room_id"]),
            device_group_id=str(value["device_group_id"]),
            baseline_id=(
                str(value["baseline_id"])
                if value.get("baseline_id")
                else None
            ),
            calibration_id=(
                str(value["calibration_id"])
                if value.get("calibration_id")
                else None
            ),
            baseline_state_json=str(value["baseline_state_json"]),
            server_url=str(value["server_url"]).rstrip("/"),
            feature_version=str(value["feature_version"]),
            preprocessing_type=str(value["preprocessing_type"]),
            setup_status=str(value["setup_status"]),
            created_at=str(value["created_at"]),
            updated_at=str(value["updated_at"]),
        )

    def updated(self, **changes: Any) -> "SetupState":
        return replace(self, updated_at=utc_now(), **changes)


class SetupStateStore:
    def __init__(self, root_dir: Path) -> None:
        self.root_dir = Path(root_dir)

    def path_for(self, room_id: str) -> Path:
        return self.root_dir / f"{room_id}.setup.json"

    def load(self, room_id: str) -> SetupState | None:
        path = self.path_for(room_id)
        if not path.exists():
            return None
        return SetupState.from_dict(
            json.loads(path.read_text(encoding="utf-8"))
        )

    def save(self, state: SetupState) -> Path:
        path = self.path_for(state.room_id)
        atomic_write_json(path, state.to_dict())
        return path


class SetupStateManager:
    """Reload setup state periodically without stopping rawlog collection."""

    def __init__(
        self,
        store: SetupStateStore,
        room_id: str,
        *,
        reload_sec: float = 10.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.store = store
        self.room_id = room_id
        self.reload_sec = reload_sec
        self.clock = clock
        self.state: SetupState | None = None
        self._last_check_at: float | None = None
        self._loaded_mtime_ns: int | None = None

    def refresh(self, *, force: bool = False) -> SetupState | None:
        now = self.clock()
        if (
            not force
            and self._last_check_at is not None
            and now - self._last_check_at < self.reload_sec
        ):
            return self.state
        self._last_check_at = now
        path = self.store.path_for(self.room_id)
        try:
            mtime_ns = path.stat().st_mtime_ns
        except OSError as exc:
            self.state = None
            self._loaded_mtime_ns = None
            LOGGER.warning(
                "setup state unavailable",
                extra={
                    "event": "setup_state_unavailable",
                    "setup_state_path": str(path),
                    "error": str(exc),
                },
            )
            return None
        if (
            self.state is not None
            and self._loaded_mtime_ns == mtime_ns
            and not force
        ):
            return self.state
        try:
            state = self.store.load(self.room_id)
            if state is None:
                raise FileNotFoundError(path)
            if state.room_id != self.room_id:
                raise ValueError("setup room_id does not match configured room")
        except Exception as exc:
            self.state = None
            self._loaded_mtime_ns = None
            LOGGER.warning(
                "setup state invalid",
                extra={
                    "event": "setup_state_invalid",
                    "setup_state_path": str(path),
                    "error": str(exc),
                },
            )
            return None
        self.state = state
        self._loaded_mtime_ns = mtime_ns
        LOGGER.info(
            "setup state loaded",
            extra={
                "event": "setup_state_loaded",
                "room_id": state.room_id,
                "setup_status": state.setup_status,
                "baseline_id": state.baseline_id,
                "calibration_id": state.calibration_id,
            },
        )
        return state
