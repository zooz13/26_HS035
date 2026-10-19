"""Persist final realtime payloads as deterministic JSON.gz artifacts."""

from __future__ import annotations

import gzip
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class PreprocessedArtifact:
    local_path: Path
    s3_bucket: str
    s3_key: str
    room_id: str
    window_id: str


class PreprocessedLogWriter:
    def __init__(
        self,
        *,
        enabled: bool,
        local_dir: Path,
        s3_bucket: str,
        s3_prefix: str = "preprocessed/",
        gzip_compress_level: int = 6,
    ) -> None:
        if not 1 <= gzip_compress_level <= 9:
            raise ValueError("gzip_compress_level must be between 1 and 9")
        self.enabled = enabled
        self.local_dir = Path(local_dir)
        self.s3_bucket = s3_bucket
        self.s3_prefix = self._normalize_prefix(s3_prefix)
        self.gzip_compress_level = gzip_compress_level

    def write(
        self,
        payload: dict[str, Any],
        *,
        room_id: str,
        window_start: float,
    ) -> PreprocessedArtifact | None:
        if not self.enabled:
            return None
        window_id = str(payload["window_id"])
        utc_timestamp = datetime.fromtimestamp(
            window_start,
            tz=timezone.utc,
        )
        output_dir = self.local_dir.joinpath(
            f"{utc_timestamp:%Y}",
            f"{utc_timestamp:%m}",
            f"{utc_timestamp:%d}",
            f"{utc_timestamp:%H}",
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        path = self._available_path(output_dir, window_id)
        self._write_gzip_json(path, payload)
        key = (
            f"{self.s3_prefix}room_id={room_id}/"
            f"year={utc_timestamp:%Y}/month={utc_timestamp:%m}/"
            f"day={utc_timestamp:%d}/hour={utc_timestamp:%H}/"
            f"{path.name}"
        )
        return PreprocessedArtifact(
            local_path=path,
            s3_bucket=self.s3_bucket,
            s3_key=key,
            room_id=room_id,
            window_id=window_id,
        )

    @staticmethod
    def _available_path(directory: Path, stem: str) -> Path:
        index = 0
        while True:
            candidate_stem = stem if index == 0 else f"{stem}_{index:03d}"
            candidate = directory / f"{candidate_stem}.json.gz"
            if not candidate.exists():
                return candidate
            index += 1

    def _write_gzip_json(
        self,
        path: Path,
        payload: dict[str, Any],
    ) -> None:
        content = (
            json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                with gzip.GzipFile(
                    filename="",
                    mode="wb",
                    compresslevel=self.gzip_compress_level,
                    fileobj=temporary,
                    mtime=0,
                ) as compressed:
                    compressed.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())
            temporary_path.replace(path)
        except Exception:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise

    @staticmethod
    def _normalize_prefix(prefix: str) -> str:
        normalized = prefix.strip().strip("/")
        if not normalized:
            raise ValueError("S3 prefix must not be empty")
        return f"{normalized}/"
