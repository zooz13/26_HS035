import gzip
import json
from pathlib import Path

from app.preprocessed_log import PreprocessedLogWriter


def test_writes_deterministic_payload_gzip_with_partitioned_key(
    tmp_path: Path,
) -> None:
    writer = PreprocessedLogWriter(
        enabled=True,
        local_dir=tmp_path / "preprocessed",
        s3_bucket="bucket",
    )
    payload = {"window_id": "room_101_20260614T000000000Z", "value": 1}

    first = writer.write(
        payload,
        room_id="room_101",
        window_start=1781395200.0,
    )
    second = writer.write(
        payload,
        room_id="room_101",
        window_start=1781395200.0,
    )

    assert first is not None and second is not None
    assert first.local_path.name == (
        "room_101_20260614T000000000Z.json.gz"
    )
    assert second.local_path.name == (
        "room_101_20260614T000000000Z_001.json.gz"
    )
    assert first.s3_key == (
        "preprocessed/room_id=room_101/year=2026/month=06/"
        "day=14/hour=00/room_101_20260614T000000000Z.json.gz"
    )
    assert json.loads(
        gzip.decompress(first.local_path.read_bytes()).decode("utf-8")
    ) == payload

