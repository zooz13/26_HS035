import gzip
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.compressor import compress_gzip
from app.jsonl_writer import WindowFileWriter
from app.models import PayloadType
from app.window_buffer import CompletedWindow


def make_window() -> CompletedWindow:
    return CompletedWindow(
        payload_type=PayloadType.RAWLOG_1M,
        window_start=1781352000.0,
        window_end=1781352060.0,
        window_sec=60,
        records=(
            {
                "schema_version": "1.0",
                "type": "csi_frame",
                "gateway_ts": 1781352000.123,
                "message": "한글",
                "data": [0, -17, -10],
                "raw_line": "CSI_DATA\t...",
            },
        ),
        frame_count=1,
        parse_error_count=0,
        rssi_min=-46,
        rssi_max=-46,
        rssi_avg=-46.0,
        noise_floor_avg=-90.0,
    )


def make_writer(
    tmp_path: Path,
    *,
    delete_source: bool = True,
) -> WindowFileWriter:
    return WindowFileWriter(
        gateway_id="gateway-laptop-001",
        device_id="esp32-rx-001",
        link_id="link-01",
        s3_bucket="csi-bucket",
        local_rawlog_dir=tmp_path / "rawlogs",
        gzip_compress_level=6,
        delete_uncompressed_after_compress=delete_source,
    )


def test_writes_compact_utf8_jsonl_with_final_newline(tmp_path: Path) -> None:
    written = make_writer(tmp_path, delete_source=False).write_window(
        make_window()
    )

    assert written.jsonl_path is not None
    content = written.jsonl_path.read_bytes()
    assert content.endswith(b"\n")
    assert "한글".encode() in content
    assert json.loads(content.decode("utf-8"))["data"] == [0, -17, -10]


def test_uses_utc_directory_and_rawlog_filename(tmp_path: Path) -> None:
    written = make_writer(tmp_path).write_window(make_window())

    assert written.data_path.relative_to(tmp_path).as_posix() == (
        "rawlogs/2026/06/13/12/120000.jsonl.gz"
    )
    assert written.metadata_path.name == "120000.metadata.json"


def test_builds_partitioned_rawlog_s3_keys(tmp_path: Path) -> None:
    written = make_writer(tmp_path).write_window(make_window())

    assert written.s3_data_key == (
        "rawlog/gateway_id=gateway-laptop-001/"
        "device_id=esp32-rx-001/link_id=link-01/"
        "year=2026/month=06/day=13/hour=12/120000.jsonl.gz"
    )
    assert written.s3_metadata_key.endswith("120000.metadata.json")


def test_collision_adds_incrementing_suffix(tmp_path: Path) -> None:
    writer = make_writer(tmp_path)

    first = writer.write_window(make_window())
    second = writer.write_window(make_window())

    assert first.data_path.name == "120000.jsonl.gz"
    assert second.data_path.name == "120000_001.jsonl.gz"
    assert second.metadata.s3_key == second.s3_data_key


def test_metadata_describes_compressed_data_file(tmp_path: Path) -> None:
    written = make_writer(tmp_path).write_window(make_window())

    metadata = json.loads(written.metadata_path.read_text(encoding="utf-8"))
    expected_hash = hashlib.sha256(written.data_path.read_bytes()).hexdigest()
    assert metadata["type"] == "rawlog_1m"
    assert metadata["window_start_iso"] == "2026-06-13T12:00:00Z"
    assert metadata["window_end_iso"] == "2026-06-13T12:01:00Z"
    assert metadata["size_bytes"] == written.data_path.stat().st_size
    assert metadata["sha256"] == expected_hash


def test_rejects_realtime_window(tmp_path: Path) -> None:
    realtime = replace(
        make_window(),
        payload_type=PayloadType.REALTIME_WINDOW,
    )

    with pytest.raises(ValueError, match="only accepts rawlog"):
        make_writer(tmp_path).write_window(realtime)


def test_deletes_uncompressed_jsonl_when_enabled(tmp_path: Path) -> None:
    written = make_writer(tmp_path, delete_source=True).write_window(
        make_window()
    )

    assert written.jsonl_path is None
    assert not written.data_path.with_suffix("").exists()


def test_deterministic_gzip_has_same_sha256(tmp_path: Path) -> None:
    source_a = tmp_path / "a.jsonl"
    source_b = tmp_path / "b.jsonl"
    source_a.write_bytes(b'{"value":1}\n')
    source_b.write_bytes(b'{"value":1}\n')
    target_a = tmp_path / "a.jsonl.gz"
    target_b = tmp_path / "b.jsonl.gz"

    compress_gzip(source_a, target_a, delete_source=False)
    compress_gzip(source_b, target_b, delete_source=False)

    assert target_a.read_bytes() == target_b.read_bytes()
    assert gzip.decompress(target_a.read_bytes()) == b'{"value":1}\n'


def test_compression_failure_keeps_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.jsonl"
    source.write_text('{"value":1}\n', encoding="utf-8")

    def fail_copy(*args: object, **kwargs: object) -> None:
        raise OSError("compression failed")

    monkeypatch.setattr("app.compressor.shutil.copyfileobj", fail_copy)

    with pytest.raises(OSError, match="compression failed"):
        compress_gzip(source, tmp_path / "source.jsonl.gz")

    assert source.exists()
