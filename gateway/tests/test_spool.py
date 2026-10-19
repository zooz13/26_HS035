import json
from pathlib import Path

from app.jsonl_writer import WrittenWindow
from app.models import PayloadType, WindowMetadata
from app.preprocessed_log import PreprocessedArtifact
from app.spool import SpoolStore


def make_window(tmp_path: Path) -> WrittenWindow:
    data_path = tmp_path / "source.jsonl.gz"
    metadata_path = tmp_path / "source.metadata.json"
    data_path.write_bytes(b"data")
    metadata_path.write_text("{}\n", encoding="utf-8")
    metadata = WindowMetadata(
        type=PayloadType.RAWLOG_1M,
        schema_version="1.0",
        gateway_id="gateway",
        device_id="device",
        link_id="link",
        window_start=1781352000.0,
        window_end=1781352060.0,
        window_start_iso="2026-06-13T12:00:00Z",
        window_end_iso="2026-06-13T12:01:00Z",
        window_sec=60,
        frame_count=1,
        estimated_fps=1 / 60,
        s3_bucket="bucket",
        s3_key="rawlog/source.jsonl.gz",
        metadata_s3_key="rawlog/source.metadata.json",
        size_bytes=4,
        sha256="hash",
        rssi_min=-46,
        rssi_max=-46,
        rssi_avg=-46.0,
        noise_floor_avg=-90.0,
        parse_error_count=0,
    )
    return WrittenWindow(
        jsonl_path=None,
        data_path=data_path,
        metadata_path=metadata_path,
        s3_data_key=metadata.s3_key,
        s3_metadata_key=metadata.metadata_s3_key,
        metadata=metadata,
    )


def test_create_moves_files_and_writes_manifest(tmp_path: Path) -> None:
    store = SpoolStore(tmp_path / "spool")
    window = make_window(tmp_path)

    manifest = store.create(
        window,
        data_uploaded=False,
        metadata_uploaded=False,
        last_error="offline",
    )

    assert manifest.spool_data_path is not None
    assert manifest.spool_data_path.read_bytes() == b"data"
    assert manifest.spool_metadata_path is not None
    assert manifest.spool_metadata_path.exists()
    manifest_path = (
        tmp_path / "spool" / "pending" / manifest.spool_id / "manifest.json"
    )
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert value["original_data_path"] == str(window.data_path)
    assert value["data_uploaded"] is False
    assert value["metadata_uploaded"] is False


def test_list_pending_round_trips_manifest(tmp_path: Path) -> None:
    store = SpoolStore(tmp_path / "spool")
    created = store.create(
        make_window(tmp_path),
        data_uploaded=False,
        metadata_uploaded=False,
        last_error="offline",
    )

    loaded = store.list_pending()

    assert len(loaded) == 1
    assert loaded[0] == created


def test_retry_failure_updates_count_and_error(tmp_path: Path) -> None:
    store = SpoolStore(tmp_path / "spool")
    manifest = store.create(
        make_window(tmp_path),
        data_uploaded=False,
        metadata_uploaded=False,
        last_error="first",
    )

    updated = store.record_retry_failure(manifest, "second")

    assert updated.retry_count == 1
    assert updated.last_error == "second"
    assert store.list_pending()[0].retry_count == 1


def test_mark_sent_keeps_only_manifest(tmp_path: Path) -> None:
    store = SpoolStore(tmp_path / "spool")
    manifest = store.create(
        make_window(tmp_path),
        data_uploaded=False,
        metadata_uploaded=False,
        last_error="offline",
    )
    assert manifest.spool_data_path is not None
    manifest.spool_data_path.unlink()
    assert manifest.spool_metadata_path is not None
    manifest.spool_metadata_path.unlink()
    manifest = store.update(
        manifest,
        spool_data_path=None,
        spool_metadata_path=None,
        data_uploaded=True,
        metadata_uploaded=True,
    )

    sent_manifest_path = store.mark_sent(manifest)

    assert sent_manifest_path.exists()
    assert not (store.pending_dir / manifest.spool_id).exists()
    assert [path.name for path in sent_manifest_path.parent.iterdir()] == [
        "manifest.json"
    ]


def test_mark_failed_moves_pending_entry(tmp_path: Path) -> None:
    store = SpoolStore(tmp_path / "spool")
    manifest = store.create(
        make_window(tmp_path),
        data_uploaded=False,
        metadata_uploaded=False,
        last_error="permanent",
    )

    failed_manifest_path = store.mark_failed(manifest)

    assert failed_manifest_path.exists()
    assert not (store.pending_dir / manifest.spool_id).exists()
    assert (failed_manifest_path.parent / "data.jsonl.gz").exists()
    assert (failed_manifest_path.parent / "metadata.json").exists()


def test_single_object_spool_round_trip_and_sent_cleanup(
    tmp_path: Path,
) -> None:
    store = SpoolStore(tmp_path / "spool")
    local_path = tmp_path / "payload.json.gz"
    local_path.write_bytes(b"payload")
    manifest = store.create_single(
        PreprocessedArtifact(
            local_path=local_path,
            s3_bucket="bucket",
            s3_key="preprocessed/payload.json.gz",
            room_id="room_101",
            window_id="window_1",
        ),
        last_error="offline",
    )

    pending = store.list_pending_single()

    assert pending == [manifest]
    assert manifest.spool_file_path.read_bytes() == b"payload"
    sent_path = store.mark_single_sent(manifest)
    assert sent_path.exists()
    assert [path.name for path in sent_path.parent.iterdir()] == [
        "manifest.json"
    ]
