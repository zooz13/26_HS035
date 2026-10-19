import json
from pathlib import Path
from urllib.error import URLError

from app.model_server_client import ModelServerClient
from app.realtime_spool import RealtimeSpoolStore


class FakeResponse:
    def __init__(self, status: int, body: bytes = b"") -> None:
        self.status = status
        self.body = body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def getcode(self) -> int:
        return self.status

    def read(self) -> bytes:
        return self.body


def payload() -> dict[str, object]:
    return {
        "window_id": "room_101_20260614T000000000Z",
        "features": {"static_score": 1.0},
        "motion_input": {"shape": [128, 192]},
    }


def test_dry_run_writes_full_payload_without_spool(tmp_path: Path) -> None:
    client = ModelServerClient(
        model_server_url="http://127.0.0.1:8000",
        room_id="room_101",
        timeout_sec=20,
        send_enabled=False,
        dryrun_dir=tmp_path / "dryrun",
        spool_store=RealtimeSpoolStore(tmp_path / "spool"),
    )

    result = client.send(payload(), frame_count=128)

    assert result.success is True
    assert result.dry_run is True
    assert result.local_payload_path is not None
    assert json.loads(
        result.local_payload_path.read_text(encoding="utf-8")
    )["window_id"] == payload()["window_id"]
    assert not (tmp_path / "spool").exists()


def test_any_2xx_response_is_success(tmp_path: Path) -> None:
    client = ModelServerClient(
        model_server_url="http://127.0.0.1:8000",
        room_id="room_101",
        timeout_sec=20,
        send_enabled=True,
        dryrun_dir=tmp_path / "dryrun",
        spool_store=RealtimeSpoolStore(tmp_path / "spool"),
        opener=lambda *args, **kwargs: FakeResponse(204),
    )

    result = client.send(payload(), frame_count=128)

    assert result.success is True
    assert result.status_code == 204
    assert not (tmp_path / "spool").exists()


def test_connection_failure_creates_realtime_spool(tmp_path: Path) -> None:
    def fail(*args: object, **kwargs: object) -> object:
        raise URLError("offline")

    store = RealtimeSpoolStore(tmp_path / "spool")
    client = ModelServerClient(
        model_server_url="http://127.0.0.1:8000",
        room_id="room_101",
        timeout_sec=20,
        send_enabled=True,
        dryrun_dir=tmp_path / "dryrun",
        spool_store=store,
        opener=fail,
    )

    result = client.send(payload(), frame_count=128)

    assert result.success is False
    pending = store.list_pending()
    assert len(pending) == 1
    assert pending[0].window_id == payload()["window_id"]

