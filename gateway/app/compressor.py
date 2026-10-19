"""Deterministic gzip compression for JSONL payloads."""

from __future__ import annotations

import gzip
import os
import shutil
import tempfile
from pathlib import Path


def compress_gzip(
    source_path: Path,
    destination_path: Path,
    *,
    compress_level: int = 6,
    delete_source: bool = True,
) -> Path:
    """Compress a file atomically with a deterministic gzip header."""

    if not 1 <= compress_level <= 9:
        raise ValueError("compress_level must be between 1 and 9")

    source_path = Path(source_path)
    destination_path = Path(destination_path)
    destination_path.parent.mkdir(parents=True, exist_ok=True)

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination_path.parent,
            prefix=f".{destination_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            with source_path.open("rb") as source_file:
                with gzip.GzipFile(
                    filename="",
                    mode="wb",
                    compresslevel=compress_level,
                    fileobj=temporary_file,
                    mtime=0,
                ) as gzip_file:
                    shutil.copyfileobj(source_file, gzip_file)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())

        temporary_path.replace(destination_path)
    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise

    if delete_source:
        source_path.unlink()
    return destination_path

