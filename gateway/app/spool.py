"""Persistent local spool for failed S3 upload pairs."""

from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from app.jsonl_writer import WrittenWindow
from app.models import (
    PayloadType,
    SingleObjectSpoolManifest,
    SpoolManifest,
)
from app.preprocessed_log import PreprocessedArtifact


class SpoolStore:
    """Manage pending, sent, and explicitly failed spool entries."""

    def __init__(self, root_dir: Path) -> None:
        self.root_dir = Path(root_dir)
        self.pending_dir = self.root_dir / "pending"
        self.sent_dir = self.root_dir / "sent"
        self.failed_dir = self.root_dir / "failed"

    def create(
        self,
        window: WrittenWindow,
        *,
        data_uploaded: bool,
        metadata_uploaded: bool,
        last_error: str,
    ) -> SpoolManifest:
        spool_id = uuid.uuid4().hex
        entry_dir = self.pending_dir / spool_id
        entry_dir.mkdir(parents=True, exist_ok=False)

        spool_data_path: Path | None = None
        spool_metadata_path: Path | None = None
        try:
            if not data_uploaded:
                spool_data_path = entry_dir / "data.jsonl.gz"
                shutil.move(str(window.data_path), spool_data_path)
            if not metadata_uploaded:
                spool_metadata_path = entry_dir / "metadata.json"
                shutil.move(str(window.metadata_path), spool_metadata_path)

            manifest = SpoolManifest(
                spool_id=spool_id,
                payload_type=window.metadata.type,
                original_data_path=window.data_path,
                original_metadata_path=window.metadata_path,
                spool_data_path=spool_data_path,
                spool_metadata_path=spool_metadata_path,
                s3_bucket=window.metadata.s3_bucket,
                s3_data_key=window.s3_data_key,
                s3_metadata_key=window.s3_metadata_key,
                data_uploaded=data_uploaded,
                metadata_uploaded=metadata_uploaded,
                created_at=datetime.now(timezone.utc),
                retry_count=0,
                last_error=last_error,
            )
            self.save(manifest)
            return manifest
        except Exception:
            if spool_data_path is not None and spool_data_path.exists():
                shutil.move(str(spool_data_path), window.data_path)
            if (
                spool_metadata_path is not None
                and spool_metadata_path.exists()
            ):
                shutil.move(str(spool_metadata_path), window.metadata_path)
            shutil.rmtree(entry_dir, ignore_errors=True)
            raise

    def list_pending(self) -> list[SpoolManifest]:
        if not self.pending_dir.exists():
            return []
        manifests: list[SpoolManifest] = []
        for path in self.pending_dir.glob("*/manifest.json"):
            value = json.loads(path.read_text(encoding="utf-8"))
            if "original_data_path" in value:
                manifests.append(SpoolManifest.from_dict(value))
        return sorted(
            manifests,
            key=lambda manifest: (manifest.created_at, manifest.spool_id),
        )

    def load(self, manifest_path: Path) -> SpoolManifest:
        value = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        return SpoolManifest.from_dict(value)

    def create_single(
        self,
        artifact: PreprocessedArtifact,
        *,
        last_error: str,
    ) -> SingleObjectSpoolManifest:
        spool_id = uuid.uuid4().hex
        entry_dir = self.pending_dir / spool_id
        entry_dir.mkdir(parents=True, exist_ok=False)
        spool_file_path = entry_dir / "data.json.gz"
        try:
            shutil.move(str(artifact.local_path), spool_file_path)
            manifest = SingleObjectSpoolManifest(
                spool_id=spool_id,
                payload_type=PayloadType.PREPROCESSED_PAYLOAD,
                local_file_path=artifact.local_path,
                spool_file_path=spool_file_path,
                s3_bucket=artifact.s3_bucket,
                s3_key=artifact.s3_key,
                created_at=datetime.now(timezone.utc),
                retry_count=0,
                last_error=last_error,
            )
            self.save_single(manifest)
            return manifest
        except Exception:
            if spool_file_path.exists():
                shutil.move(str(spool_file_path), artifact.local_path)
            shutil.rmtree(entry_dir, ignore_errors=True)
            raise

    def list_pending_single(self) -> list[SingleObjectSpoolManifest]:
        if not self.pending_dir.exists():
            return []
        manifests: list[SingleObjectSpoolManifest] = []
        for path in self.pending_dir.glob("*/manifest.json"):
            value = json.loads(path.read_text(encoding="utf-8"))
            if (
                value.get("payload_type")
                == PayloadType.PREPROCESSED_PAYLOAD.value
                and "spool_file_path" in value
            ):
                manifests.append(
                    SingleObjectSpoolManifest.from_dict(value)
                )
        return sorted(
            manifests,
            key=lambda value: (value.created_at, value.spool_id),
        )

    def save_single(
        self,
        manifest: SingleObjectSpoolManifest,
    ) -> None:
        path = self.pending_dir / manifest.spool_id / "manifest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        self._write_manifest_value(path, manifest.to_dict())

    def record_single_retry_failure(
        self,
        manifest: SingleObjectSpoolManifest,
        error: str,
    ) -> SingleObjectSpoolManifest:
        updated = replace(
            manifest,
            retry_count=manifest.retry_count + 1,
            last_error=error,
        )
        self.save_single(updated)
        return updated

    def mark_single_sent(
        self,
        manifest: SingleObjectSpoolManifest,
    ) -> Path:
        source_dir = self.pending_dir / manifest.spool_id
        target_dir = self.sent_dir / manifest.spool_id
        target_dir.mkdir(parents=True, exist_ok=False)
        sent_manifest = replace(manifest, last_error=None)
        self._write_manifest_value(
            target_dir / "manifest.json",
            sent_manifest.to_dict(),
        )
        shutil.rmtree(source_dir)
        return target_dir / "manifest.json"

    def save(self, manifest: SpoolManifest) -> None:
        manifest_path = self._manifest_path(manifest)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = manifest_path.with_suffix(".json.tmp")
        temporary_path.write_text(
            json.dumps(
                manifest.to_dict(),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(manifest_path)

    def update(
        self,
        manifest: SpoolManifest,
        **changes: object,
    ) -> SpoolManifest:
        updated = replace(manifest, **changes)
        self.save(updated)
        return updated

    def record_retry_failure(
        self,
        manifest: SpoolManifest,
        error: str,
    ) -> SpoolManifest:
        return self.update(
            manifest,
            retry_count=manifest.retry_count + 1,
            last_error=error,
        )

    def mark_sent(self, manifest: SpoolManifest) -> Path:
        source_dir = self.pending_dir / manifest.spool_id
        target_dir = self.sent_dir / manifest.spool_id
        target_dir.mkdir(parents=True, exist_ok=False)

        sent_manifest = replace(
            manifest,
            spool_data_path=None,
            spool_metadata_path=None,
            data_uploaded=True,
            metadata_uploaded=True,
            last_error=None,
        )
        self._write_manifest(target_dir / "manifest.json", sent_manifest)
        shutil.rmtree(source_dir)
        return target_dir / "manifest.json"

    def mark_failed(self, manifest: SpoolManifest) -> Path:
        source_dir = self.pending_dir / manifest.spool_id
        target_dir = self.failed_dir / manifest.spool_id
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source_dir), target_dir)
        return target_dir / "manifest.json"

    def _manifest_path(self, manifest: SpoolManifest) -> Path:
        return self.pending_dir / manifest.spool_id / "manifest.json"

    @staticmethod
    def _write_manifest(path: Path, manifest: SpoolManifest) -> None:
        SpoolStore._write_manifest_value(path, manifest.to_dict())

    @staticmethod
    def _write_manifest_value(path: Path, value: dict[str, object]) -> None:
        path.write_text(
            json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
