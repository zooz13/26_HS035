"""Write completed windows as JSONL.gz and paired metadata JSON."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.compressor import compress_gzip
from app.models import (
    SCHEMA_VERSION,
    PayloadType,
    WindowMetadata,
    epoch_utc_isoformat,
)
from app.window_buffer import CompletedWindow


@dataclass(frozen=True, slots=True)
class WrittenWindow:
    jsonl_path: Path | None
    data_path: Path
    metadata_path: Path
    s3_data_key: str
    s3_metadata_key: str
    metadata: WindowMetadata


class WindowFileWriter:
    """Persist rawlog windows using UTC paths and collision-safe names."""

    def __init__(
        self,
        *,
        gateway_id: str,
        device_id: str,
        link_id: str,
        s3_bucket: str,
        local_rawlog_dir: Path,
        s3_rawlog_prefix: str = "rawlog/",
        gzip_compress_level: int = 6,
        delete_uncompressed_after_compress: bool = True,
    ) -> None:
        if not 1 <= gzip_compress_level <= 9:
            raise ValueError("gzip_compress_level must be between 1 and 9")

        self.gateway_id = gateway_id
        self.device_id = device_id
        self.link_id = link_id
        self.s3_bucket = s3_bucket
        self.local_rawlog_dir = Path(local_rawlog_dir)
        self.s3_rawlog_prefix = self._normalize_prefix(s3_rawlog_prefix)
        self.gzip_compress_level = gzip_compress_level
        self.delete_uncompressed_after_compress = (
            delete_uncompressed_after_compress
        )

    def write_window(self, window: CompletedWindow) -> WrittenWindow:
        if window.payload_type is not PayloadType.RAWLOG_1M:
            raise ValueError("WindowFileWriter only accepts rawlog windows")
        window_datetime = datetime.fromtimestamp(
            window.window_start,
            tz=timezone.utc,
        )
        output_dir = self._output_dir(window.payload_type, window_datetime)
        output_dir.mkdir(parents=True, exist_ok=True)

        stem = self._available_stem(
            output_dir,
            self._base_stem(window.payload_type, window_datetime),
        )
        jsonl_path = output_dir / f"{stem}.jsonl"
        data_path = output_dir / f"{stem}.jsonl.gz"
        metadata_path = output_dir / f"{stem}.metadata.json"

        self._write_jsonl(jsonl_path, window.records)
        compress_gzip(
            jsonl_path,
            data_path,
            compress_level=self.gzip_compress_level,
            delete_source=self.delete_uncompressed_after_compress,
        )

        s3_data_key = self._s3_key(
            window.payload_type,
            window_datetime,
            data_path.name,
        )
        s3_metadata_key = self._metadata_key_from_data_key(s3_data_key)
        metadata = WindowMetadata(
            type=window.payload_type,
            schema_version=SCHEMA_VERSION,
            gateway_id=self.gateway_id,
            device_id=self.device_id,
            link_id=self.link_id,
            window_start=window.window_start,
            window_end=window.window_end,
            window_start_iso=epoch_utc_isoformat(window.window_start),
            window_end_iso=epoch_utc_isoformat(window.window_end),
            window_sec=window.window_sec,
            frame_count=window.frame_count,
            estimated_fps=window.estimated_fps,
            s3_bucket=self.s3_bucket,
            s3_key=s3_data_key,
            metadata_s3_key=s3_metadata_key,
            size_bytes=data_path.stat().st_size,
            sha256=self._sha256(data_path),
            rssi_min=window.rssi_min,
            rssi_max=window.rssi_max,
            rssi_avg=window.rssi_avg,
            noise_floor_avg=window.noise_floor_avg,
            parse_error_count=window.parse_error_count,
        )
        self._write_json(metadata_path, metadata.to_dict())

        return WrittenWindow(
            jsonl_path=(
                None
                if self.delete_uncompressed_after_compress
                else jsonl_path
            ),
            data_path=data_path,
            metadata_path=metadata_path,
            s3_data_key=s3_data_key,
            s3_metadata_key=s3_metadata_key,
            metadata=metadata,
        )

    def _output_dir(
        self,
        payload_type: PayloadType,
        window_datetime: datetime,
    ) -> Path:
        return self.local_rawlog_dir.joinpath(
            window_datetime.strftime("%Y"),
            window_datetime.strftime("%m"),
            window_datetime.strftime("%d"),
            window_datetime.strftime("%H"),
        )

    @staticmethod
    def _base_stem(
        payload_type: PayloadType,
        window_datetime: datetime,
    ) -> str:
        return window_datetime.strftime("%H%M%S")

    @staticmethod
    def _available_stem(output_dir: Path, base_stem: str) -> str:
        suffix = 0
        while True:
            stem = base_stem if suffix == 0 else f"{base_stem}_{suffix:03d}"
            candidates = (
                output_dir / f"{stem}.jsonl",
                output_dir / f"{stem}.jsonl.gz",
                output_dir / f"{stem}.metadata.json",
            )
            if not any(path.exists() for path in candidates):
                return stem
            suffix += 1

    def _s3_key(
        self,
        payload_type: PayloadType,
        window_datetime: datetime,
        filename: str,
    ) -> str:
        partition = (
            f"gateway_id={self.gateway_id}/"
            f"device_id={self.device_id}/"
            f"link_id={self.link_id}/"
            f"year={window_datetime:%Y}/"
            f"month={window_datetime:%m}/"
            f"day={window_datetime:%d}/"
            f"hour={window_datetime:%H}/"
        )
        return f"{self.s3_rawlog_prefix}{partition}{filename}"

    @staticmethod
    def _metadata_key_from_data_key(data_key: str) -> str:
        suffix = ".jsonl.gz"
        if not data_key.endswith(suffix):
            raise ValueError("data key must end with .jsonl.gz")
        return f"{data_key[:-len(suffix)]}.metadata.json"

    @staticmethod
    def _normalize_prefix(prefix: str) -> str:
        normalized = prefix.strip().strip("/")
        if not normalized:
            raise ValueError("S3 prefix must not be empty")
        return f"{normalized}/"

    @staticmethod
    def _write_jsonl(
        destination: Path,
        records: tuple[dict[str, Any], ...],
    ) -> None:
        lines = (
            json.dumps(record, ensure_ascii=False, separators=(",", ":"))
            for record in records
        )
        content = "".join(f"{line}\n" for line in lines)
        WindowFileWriter._atomic_write_text(destination, content)

    @staticmethod
    def _write_json(destination: Path, value: dict[str, Any]) -> None:
        content = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        WindowFileWriter._atomic_write_text(destination, f"{content}\n")

    @staticmethod
    def _atomic_write_text(destination: Path, content: str) -> None:
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
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                temporary_file.write(content)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            temporary_path.replace(destination)
        except Exception:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
