"""Persistent spool for failed realtime HTTP payloads."""

from __future__ import annotations

import json
import shutil
import tempfile
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.models import RealtimeSpoolManifest


class RealtimeSpoolStore:
    def __init__(self, root_dir: Path) -> None:
        self.root_dir = Path(root_dir)
        self.pending_dir = self.root_dir / "pending"
        self.sent_dir = self.root_dir / "sent"
        self.failed_dir = self.root_dir / "failed"

    def create(
        self,
        *,
        room_id: str,
        window_id: str,
        endpoint: str,
        payload: dict[str, Any],
        last_error: str,
    ) -> RealtimeSpoolManifest:
        spool_id = uuid.uuid4().hex
        entry_dir = self.pending_dir / spool_id
        entry_dir.mkdir(parents=True, exist_ok=False)
        payload_path = entry_dir / "payload.json"
        self._write_json(payload_path, payload)
        manifest = RealtimeSpoolManifest(
            spool_id=spool_id,
            room_id=room_id,
            window_id=window_id,
            endpoint=endpoint,
            local_payload_path=payload_path,
            created_at=datetime.now(timezone.utc),
            retry_count=0,
            last_error=last_error,
            last_attempt_at=datetime.now(timezone.utc),
        )
        self.save(manifest)
        return manifest

    def list_pending(self) -> list[RealtimeSpoolManifest]:
        if not self.pending_dir.exists():
            return []
        manifests = [
            RealtimeSpoolManifest.from_dict(
                json.loads(path.read_text(encoding="utf-8"))
            )
            for path in self.pending_dir.glob("*/manifest.json")
        ]
        return sorted(
            manifests,
            key=lambda value: (value.created_at, value.spool_id),
        )

    def load_payload(
        self,
        manifest: RealtimeSpoolManifest,
    ) -> dict[str, Any]:
        return json.loads(
            manifest.local_payload_path.read_text(encoding="utf-8")
        )

    def save(self, manifest: RealtimeSpoolManifest) -> None:
        path = self.pending_dir / manifest.spool_id / "manifest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        self._write_json(path, manifest.to_dict())

    def record_failure(
        self,
        manifest: RealtimeSpoolManifest,
        error: str,
    ) -> RealtimeSpoolManifest:
        updated = replace(
            manifest,
            retry_count=manifest.retry_count + 1,
            last_error=error,
            last_attempt_at=datetime.now(timezone.utc),
        )
        self.save(updated)
        return updated

    def mark_sent(self, manifest: RealtimeSpoolManifest) -> Path:
        source_dir = self.pending_dir / manifest.spool_id
        target_dir = self.sent_dir / manifest.spool_id
        target_dir.mkdir(parents=True, exist_ok=False)
        sent_manifest = replace(
            manifest,
            last_error=None,
            last_attempt_at=datetime.now(timezone.utc),
        )
        self._write_json(
            target_dir / "manifest.json",
            sent_manifest.to_dict(),
        )
        shutil.rmtree(source_dir)
        return target_dir / "manifest.json"

    def mark_failed(self, manifest: RealtimeSpoolManifest) -> Path:
        source_dir = self.pending_dir / manifest.spool_id
        target_dir = self.failed_dir / manifest.spool_id
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source_dir), target_dir)
        return target_dir / "manifest.json"

    @staticmethod
    def _write_json(path: Path, value: dict[str, Any]) -> None:
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
                    value,
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

