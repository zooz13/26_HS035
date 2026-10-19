from pathlib import Path

from app.realtime_spool import RealtimeSpoolStore


def test_retry_failure_updates_manifest_and_sent_keeps_only_manifest(
    tmp_path: Path,
) -> None:
    store = RealtimeSpoolStore(tmp_path / "spool")
    manifest = store.create(
        room_id="room_101",
        window_id="window_1",
        endpoint="http://127.0.0.1:8000/api/rooms/room_101/window",
        payload={"window_id": "window_1"},
        last_error="offline",
    )

    updated = store.record_failure(manifest, "still offline")

    assert updated.retry_count == 1
    assert updated.last_attempt_at is not None
    sent_path = store.mark_sent(updated)
    assert sent_path.exists()
    assert [path.name for path in sent_path.parent.iterdir()] == [
        "manifest.json"
    ]

