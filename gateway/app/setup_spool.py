"""Persistent spool for failed baseline/calibration finish requests."""

from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.setup_state import atomic_write_json


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class SetupSpoolManifest:
    spool_id: str
    operation: str
    room_id: str
    endpoint: str
    payload_path: str
    source_payload_path: str
    created_at: str
    retry_count: int
    last_error: str | None
    last_attempt_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SetupSpoolManifest":
        return cls(**value)


class SetupSpoolStore:
    def __init__(self, root_dir: Path) -> None:
        self.root_dir = Path(root_dir)
        self.pending_dir = self.root_dir / "pending"
        self.sent_dir = self.root_dir / "sent"
        self.failed_dir = self.root_dir / "failed"

    def create(
        self,
        *,
        envelope: dict[str, Any],
        source_payload_path: Path,
        last_error: str,
    ) -> SetupSpoolManifest:
        spool_id = uuid.uuid4().hex
        entry_dir = self.pending_dir / spool_id
        entry_dir.mkdir(parents=True, exist_ok=False)
        payload_path = entry_dir / "payload.json"
        atomic_write_json(payload_path, envelope)
        operation = str(envelope["operation"])
        room_id = str(envelope["room_id"])
        endpoint_name = operation.replace("_", "/")
        manifest = SetupSpoolManifest(
            spool_id=spool_id,
            operation=operation,
            room_id=room_id,
            endpoint=(
                f"{str(envelope['server_url']).rstrip('/')}/api/rooms/"
                f"{room_id}/{endpoint_name}"
            ),
            payload_path=str(payload_path),
            source_payload_path=str(source_payload_path),
            created_at=_utc_now(),
            retry_count=0,
            last_error=last_error,
            last_attempt_at=_utc_now(),
        )
        self.save(manifest)
        return manifest

    def list_pending(self) -> list[SetupSpoolManifest]:
        if not self.pending_dir.exists():
            return []
        manifests = [
            SetupSpoolManifest.from_dict(
                json.loads(path.read_text(encoding="utf-8"))
            )
            for path in self.pending_dir.glob("*/manifest.json")
        ]
        return sorted(manifests, key=lambda item: (item.created_at, item.spool_id))

    def load_envelope(
        self,
        manifest: SetupSpoolManifest,
    ) -> dict[str, Any]:
        return json.loads(Path(manifest.payload_path).read_text(encoding="utf-8"))

    def save(self, manifest: SetupSpoolManifest) -> None:
        path = self.pending_dir / manifest.spool_id / "manifest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, manifest.to_dict())

    def record_failure(
        self,
        manifest: SetupSpoolManifest,
        error: str,
    ) -> SetupSpoolManifest:
        updated = replace(
            manifest,
            retry_count=manifest.retry_count + 1,
            last_error=error,
            last_attempt_at=_utc_now(),
        )
        self.save(updated)
        return updated

    def mark_sent(self, manifest: SetupSpoolManifest) -> Path:
        source_dir = self.pending_dir / manifest.spool_id
        target_dir = self.sent_dir / manifest.spool_id
        target_dir.mkdir(parents=True, exist_ok=False)
        sent = replace(
            manifest,
            last_error=None,
            last_attempt_at=_utc_now(),
        )
        atomic_write_json(target_dir / "manifest.json", sent.to_dict())
        shutil.rmtree(source_dir)
        return target_dir / "manifest.json"
