import json
import logging
from pathlib import Path
from urllib.error import URLError

from app.logging_config import JsonLogFormatter
from app.jsonl_writer import WrittenWindow
from app.models import PayloadType, WindowMetadata
from app.preprocessed_log import PreprocessedArtifact
from app.s3_uploader import S3Uploader
from app.spool import SpoolStore


class FakeResponse:
    def __init__(self, status_code: int, body: bytes = b"") -> None:
        self.status_code = status_code
        self.body = body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def getcode(self) -> int:
        return self.status_code

    def read(self) -> bytes:
        return self.body


class RecordingOpener:
    def __init__(
        self,
        *,
        url_prefix: str = "https://s3.example.test/upload",
        fail_presign_at: set[int] | None = None,
        fail_put_at: set[int] | None = None,
    ) -> None:
        self.url_prefix = url_prefix
        self.fail_presign_at = fail_presign_at or set()
        self.fail_put_at = fail_put_at or set()
        self.requests: list[object] = []
        self.presign_count = 0
        self.put_count = 0

    def __call__(self, request: object, *, timeout: float) -> FakeResponse:
        assert timeout == 20.0
        self.requests.append(request)
        method = request.get_method()
        if method == "POST":
            self.presign_count += 1
            if self.presign_count in self.fail_presign_at:
                raise URLError("model server unavailable")
            payload = json.loads(request.data.decode("utf-8"))
            headers = {"Content-Type": payload["content_type"]}
            if "content_encoding" in payload:
                headers["Content-Encoding"] = payload["content_encoding"]
            body = json.dumps(
                {
                    "url": f"{self.url_prefix}?signature={self.presign_count}",
                    "method": "PUT",
                    "headers": headers,
                    "expires_in": 900,
                }
            ).encode("utf-8")
            return FakeResponse(200, body)
        if method == "PUT":
            self.put_count += 1
            if self.put_count in self.fail_put_at:
                raise URLError("S3 unavailable")
            return FakeResponse(200)
        raise AssertionError(f"unexpected method: {method}")


def make_written_window(tmp_path: Path) -> WrittenWindow:
    jsonl_path = tmp_path / "120000_000.jsonl"
    data_path = tmp_path / "120000_000.jsonl.gz"
    metadata_path = tmp_path / "120000_000.metadata.json"
    jsonl_path.write_text("{}\n", encoding="utf-8")
    data_path.write_bytes(b"gzip-data")
    metadata_path.write_text("{}\n", encoding="utf-8")
    metadata = WindowMetadata(
        type=PayloadType.RAWLOG_1M,
        schema_version="1.0",
        gateway_id="gateway-laptop-001",
        device_id="esp32-rx-001",
        link_id="link-01",
        window_start=1781352000.0,
        window_end=1781352005.0,
        window_start_iso="2026-06-13T12:00:00Z",
        window_end_iso="2026-06-13T12:00:05Z",
        window_sec=5,
        frame_count=10,
        estimated_fps=2.0,
        s3_bucket="legacy-metadata-bucket",
        s3_key="rawlog/120000.jsonl.gz",
        metadata_s3_key="rawlog/120000.metadata.json",
        size_bytes=9,
        sha256="abc123",
        rssi_min=-50,
        rssi_max=-40,
        rssi_avg=-45.0,
        noise_floor_avg=-90.0,
        parse_error_count=0,
    )
    return WrittenWindow(
        jsonl_path=jsonl_path,
        data_path=data_path,
        metadata_path=metadata_path,
        s3_data_key=metadata.s3_key,
        s3_metadata_key=metadata.metadata_s3_key,
        metadata=metadata,
    )


def make_uploader(
    tmp_path: Path,
    opener: RecordingOpener,
    *,
    enabled: bool = True,
    delete_local: bool = False,
) -> S3Uploader:
    return S3Uploader(
        s3_upload_enabled=enabled,
        delete_local_after_upload=delete_local,
        spool_store=SpoolStore(tmp_path / "spool"),
        model_server_url="http://model-server:8000",
        timeout_sec=20.0,
        opener=opener,
    )


def requests_with_method(opener: RecordingOpener, method: str) -> list[object]:
    return [
        request
        for request in opener.requests
        if request.get_method() == method
    ]


def test_presigns_and_puts_data_before_metadata_with_matching_headers(
    tmp_path: Path,
    caplog,
) -> None:
    opener = RecordingOpener()
    window = make_written_window(tmp_path)

    with caplog.at_level(logging.INFO, logger="app.s3_uploader"):
        result = make_uploader(tmp_path, opener).upload(window)

    assert result.success is True
    assert [request.get_method() for request in opener.requests] == [
        "POST",
        "PUT",
        "POST",
        "PUT",
    ]
    presign_requests = requests_with_method(opener, "POST")
    data_payload = json.loads(presign_requests[0].data.decode("utf-8"))
    metadata_payload = json.loads(presign_requests[1].data.decode("utf-8"))
    assert data_payload == {
        "key": window.s3_data_key,
        "content_type": "application/x-ndjson",
        "content_encoding": "gzip",
    }
    assert metadata_payload == {
        "key": window.s3_metadata_key,
        "content_type": "application/json",
    }
    assert "bucket" not in data_payload
    put_requests = requests_with_method(opener, "PUT")
    assert put_requests[0].get_header("Content-type") == "application/x-ndjson"
    assert put_requests[0].get_header("Content-encoding") == "gzip"
    assert put_requests[0].data == b"gzip-data"
    assert put_requests[1].get_header("Content-type") == "application/json"
    success_records = [
        record
        for record in caplog.records
        if record.getMessage().startswith(
            "\\" * 20 + " [ S3 UPLOAD SUCCESS ]"
        )
    ]
    success_logs = [record.getMessage() for record in success_records]
    assert len(success_logs) == 2
    assert (
        f"key={window.s3_data_key} | status=200 | "
        "type=application/x-ndjson | encoding=gzip"
    ) in success_logs[0]
    assert (
        f"key={window.s3_metadata_key} | status=200 | "
        "type=application/json | encoding=none"
    ) in success_logs[1]
    assert JsonLogFormatter().format(success_records[0]).splitlines() == [
        "\\" * 20 + " [ S3 UPLOAD SUCCESS ] " + "/" * 20,
        f"key={window.s3_data_key} | status=200 | "
        "type=application/x-ndjson | encoding=gzip",
        "/" * 63,
    ]


def test_dry_run_does_not_request_presign_upload_spool_or_delete(
    tmp_path: Path,
) -> None:
    opener = RecordingOpener()
    window = make_written_window(tmp_path)

    result = make_uploader(
        tmp_path,
        opener,
        enabled=False,
        delete_local=True,
    ).upload(window)

    assert result.success is True
    assert result.dry_run is True
    assert opener.requests == []
    assert window.jsonl_path is not None and window.jsonl_path.exists()
    assert window.data_path.exists()
    assert window.metadata_path.exists()
    assert not (tmp_path / "spool").exists()


def test_success_deletes_all_local_artifacts_when_enabled(
    tmp_path: Path,
) -> None:
    opener = RecordingOpener()
    window = make_written_window(tmp_path)

    result = make_uploader(
        tmp_path,
        opener,
        delete_local=True,
    ).upload(window)

    assert result.success is True
    assert window.jsonl_path is not None and not window.jsonl_path.exists()
    assert not window.data_path.exists()
    assert not window.metadata_path.exists()


def test_presign_api_failure_spools_data_and_metadata(tmp_path: Path) -> None:
    opener = RecordingOpener(fail_presign_at={1})
    window = make_written_window(tmp_path)

    result = make_uploader(tmp_path, opener).upload(window)

    assert result.success is False
    assert result.spool_manifest is not None
    manifest = result.spool_manifest
    assert manifest.data_uploaded is False
    assert manifest.metadata_uploaded is False
    assert manifest.spool_data_path is not None
    assert manifest.spool_data_path.exists()
    assert manifest.spool_metadata_path is not None
    assert manifest.spool_metadata_path.exists()
    assert opener.put_count == 0


def test_s3_put_failure_spools_data_and_metadata(
    tmp_path: Path,
    caplog,
) -> None:
    opener = RecordingOpener(fail_put_at={1})
    window = make_written_window(tmp_path)

    with caplog.at_level(logging.INFO, logger="app.s3_uploader"):
        result = make_uploader(tmp_path, opener).upload(window)

    assert result.success is False
    assert result.spool_manifest is not None
    assert result.spool_manifest.data_uploaded is False
    assert result.spool_manifest.spool_data_path is not None
    assert result.spool_manifest.spool_metadata_path is not None
    assert "S3 UPLOAD SUCCESS" not in caplog.text


def test_metadata_failure_spools_only_metadata(tmp_path: Path) -> None:
    opener = RecordingOpener(fail_put_at={2})
    window = make_written_window(tmp_path)

    result = make_uploader(tmp_path, opener).upload(window)

    assert result.success is False
    assert result.spool_manifest is not None
    manifest = result.spool_manifest
    assert manifest.data_uploaded is True
    assert manifest.metadata_uploaded is False
    assert manifest.spool_data_path is None
    assert manifest.spool_metadata_path is not None
    assert manifest.spool_metadata_path.exists()


def test_retry_requests_a_fresh_url_and_skips_uploaded_data(
    tmp_path: Path,
    caplog,
) -> None:
    first_opener = RecordingOpener(
        url_prefix="https://s3.example.test/expired",
        fail_put_at={2},
    )
    window = make_written_window(tmp_path)
    failed = make_uploader(tmp_path, first_opener).upload(window)
    assert failed.spool_manifest is not None
    caplog.clear()

    retry_opener = RecordingOpener(
        url_prefix="https://s3.example.test/fresh"
    )
    with caplog.at_level(logging.INFO, logger="app.s3_uploader"):
        results = make_uploader(tmp_path, retry_opener).retry_pending()

    assert results[0].success is True
    assert [request.get_method() for request in retry_opener.requests] == [
        "POST",
        "PUT",
    ]
    presign_payload = json.loads(retry_opener.requests[0].data.decode("utf-8"))
    assert presign_payload["key"] == window.s3_metadata_key
    assert retry_opener.requests[1].full_url.startswith(
        "https://s3.example.test/fresh"
    )
    manifest_json = next((tmp_path / "spool" / "sent").glob("*/manifest.json"))
    assert "url" not in json.loads(manifest_json.read_text(encoding="utf-8"))
    assert "S3 UPLOAD SUCCESS" in caplog.text
    assert f"key={window.s3_metadata_key}" in caplog.text
    assert "status=200" in caplog.text


def test_uploads_preprocessed_with_json_gzip_headers(
    tmp_path: Path,
    caplog,
) -> None:
    opener = RecordingOpener()
    path = tmp_path / "payload.json.gz"
    path.write_bytes(b"gzip")
    artifact = PreprocessedArtifact(
        local_path=path,
        s3_bucket="legacy-metadata-bucket",
        s3_key="preprocessed/room_id=room_101/payload.json.gz",
        room_id="room_101",
        window_id="window_1",
    )

    with caplog.at_level(logging.INFO, logger="app.s3_uploader"):
        result = make_uploader(tmp_path, opener).upload_preprocessed(artifact)

    assert result.success is True
    presign_payload = json.loads(opener.requests[0].data.decode("utf-8"))
    assert presign_payload == {
        "key": artifact.s3_key,
        "content_type": "application/json",
        "content_encoding": "gzip",
    }
    put_request = opener.requests[1]
    assert put_request.get_header("Content-type") == "application/json"
    assert put_request.get_header("Content-encoding") == "gzip"
    assert "S3 UPLOAD SUCCESS" in caplog.text
    assert (
        f"key={artifact.s3_key} | status=200 | "
        "type=application/json | encoding=gzip"
    ) in caplog.text


def test_preprocessed_failure_moves_single_file_to_spool(
    tmp_path: Path,
) -> None:
    opener = RecordingOpener(fail_put_at={1})
    path = tmp_path / "payload.json.gz"
    path.write_bytes(b"gzip")
    artifact = PreprocessedArtifact(
        local_path=path,
        s3_bucket="legacy-metadata-bucket",
        s3_key="preprocessed/payload.json.gz",
        room_id="room_101",
        window_id="window_1",
    )
    uploader = make_uploader(tmp_path, opener)

    result = uploader.upload_preprocessed(artifact)

    assert result.success is False
    assert not path.exists()
    pending = uploader.spool_store.list_pending_single()
    assert len(pending) == 1
    assert pending[0].spool_file_path.read_bytes() == b"gzip"
