from pathlib import Path

from app.setup_spool import SetupSpoolStore


def envelope() -> dict:
    return {
        "operation": "baseline_finish",
        "room_id": "room_101",
        "server_url": "http://127.0.0.1:8000",
        "payload": {"baseline_id": "baseline_room_101_v001"},
    }


def test_setup_spool_preserves_payload_and_tracks_retry(tmp_path: Path) -> None:
    store = SetupSpoolStore(tmp_path)
    source = tmp_path / "source.json"
    source.write_text("{}", encoding="utf-8")

    manifest = store.create(
        envelope=envelope(),
        source_payload_path=source,
        last_error="offline",
    )
    updated = store.record_failure(manifest, "still offline")

    assert store.load_envelope(updated) == envelope()
    assert updated.retry_count == 1
    assert updated.last_error == "still offline"


def test_setup_spool_success_keeps_only_sent_manifest(tmp_path: Path) -> None:
    store = SetupSpoolStore(tmp_path)
    source = tmp_path / "source.json"
    source.write_text("{}", encoding="utf-8")
    manifest = store.create(
        envelope=envelope(),
        source_payload_path=source,
        last_error="offline",
    )

    sent_path = store.mark_sent(manifest)

    assert sent_path.exists()
    assert not (store.pending_dir / manifest.spool_id).exists()
    assert list(sent_path.parent.iterdir()) == [sent_path]
