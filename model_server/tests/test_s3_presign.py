from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient


ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from api.server import create_app


def test_app_startup_does_not_resolve_aws_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boto_client = Mock(side_effect=RuntimeError("credentials unavailable"))
    monkeypatch.setattr("model_server.s3_presigner.boto3.client", boto_client)

    app = create_app()
    response = TestClient(app).get("/api/health")

    assert response.status_code == 200
    boto_client.assert_not_called()


@pytest.fixture
def presign_client() -> tuple[TestClient, Mock]:
    app = create_app()
    s3_client = Mock()
    s3_client.generate_presigned_url.return_value = (
        "https://s3.example.test/upload?signature=fresh"
    )
    app.state.s3_presigner.client_factory = Mock(return_value=s3_client)
    return TestClient(app), s3_client


def test_presigns_rawlog_put_with_signed_headers_and_ttl(
    presign_client: tuple[TestClient, Mock],
) -> None:
    client, s3_client = presign_client

    response = client.post(
        "/api/s3/presign-upload",
        json={
            "key": "rawlog/gateway_id=gateway-1/data.jsonl.gz",
            "content_type": "application/x-ndjson",
            "content_encoding": "gzip",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "url": "https://s3.example.test/upload?signature=fresh",
        "method": "PUT",
        "headers": {
            "Content-Type": "application/x-ndjson",
            "Content-Encoding": "gzip",
        },
        "expires_in": 900,
    }
    s3_client.generate_presigned_url.assert_called_once_with(
        "put_object",
        Params={
            "Bucket": "project6-52-oregon-csi-project-s3",
            "Key": "rawlog/gateway_id=gateway-1/data.jsonl.gz",
            "ContentType": "application/x-ndjson",
            "ContentEncoding": "gzip",
        },
        ExpiresIn=900,
        HttpMethod="PUT",
    )


def test_presigns_preprocessed_key(
    presign_client: tuple[TestClient, Mock],
) -> None:
    client, _ = presign_client

    response = client.post(
        "/api/s3/presign-upload",
        json={
            "key": "preprocessed/room_id=room-1/payload.json.gz",
            "content_type": "application/json",
            "content_encoding": "gzip",
        },
    )

    assert response.status_code == 200
    assert response.json()["headers"]["Content-Type"] == "application/json"


@pytest.mark.parametrize(
    "key",
    [
        "other/data.json",
        "rawlog/../secret.json",
        "rawlog/path..segment/data.json",
        "/rawlog/data.json",
        "rawlog\\data.json",
        "rawlog//data.json",
    ],
)
def test_rejects_key_outside_allowlist_or_with_unsafe_path(
    presign_client: tuple[TestClient, Mock],
    key: str,
) -> None:
    client, s3_client = presign_client

    response = client.post(
        "/api/s3/presign-upload",
        json={"key": key, "content_type": "application/json"},
    )

    assert 400 <= response.status_code < 500
    s3_client.generate_presigned_url.assert_not_called()


def test_missing_required_field_returns_422(
    presign_client: tuple[TestClient, Mock],
) -> None:
    client, s3_client = presign_client

    response = client.post(
        "/api/s3/presign-upload",
        json={"key": "rawlog/data.json"},
    )

    assert response.status_code == 422
    s3_client.generate_presigned_url.assert_not_called()


def test_aws_client_error_returns_service_unavailable(
    presign_client: tuple[TestClient, Mock],
) -> None:
    client, s3_client = presign_client
    s3_client.generate_presigned_url.side_effect = RuntimeError(
        "credentials unavailable"
    )

    response = client.post(
        "/api/s3/presign-upload",
        json={
            "key": "rawlog/data.json",
            "content_type": "application/json",
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"] == (
        "S3 upload URL is temporarily unavailable"
    )
