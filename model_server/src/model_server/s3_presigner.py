from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

import boto3


SUPPORTED_CONTENT_TYPES = {"application/json", "application/x-ndjson"}
SUPPORTED_CONTENT_ENCODINGS = {"gzip"}


class PresignValidationError(ValueError):
    pass


class PresignGenerationError(RuntimeError):
    pass


class S3Presigner:
    """Issue constrained S3 PUT URLs using boto3's default credential chain."""

    def __init__(
        self,
        *,
        bucket: str,
        region: str,
        expires_in: int,
        allowed_prefixes: Sequence[str],
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        if not bucket.strip():
            raise ValueError("S3 presign bucket must not be empty")
        if not region.strip():
            raise ValueError("S3 presign region must not be empty")
        if expires_in <= 0:
            raise ValueError("S3 presign expires_in must be greater than zero")
        prefixes = tuple(self._normalize_prefix(value) for value in allowed_prefixes)
        if not prefixes:
            raise ValueError("at least one S3 presign prefix is required")

        self.bucket = bucket.strip()
        self.region = region.strip()
        self.expires_in = expires_in
        self.allowed_prefixes = prefixes
        self.client_factory = client_factory or self._build_client

    def generate(
        self,
        *,
        key: str,
        content_type: str,
        content_encoding: str | None = None,
    ) -> tuple[str, dict[str, str]]:
        self.validate_key(key)
        normalized_content_type = content_type.strip().lower()
        if normalized_content_type not in SUPPORTED_CONTENT_TYPES:
            raise PresignValidationError("unsupported content_type")

        normalized_encoding = None
        if content_encoding is not None:
            normalized_encoding = content_encoding.strip().lower()
            if normalized_encoding not in SUPPORTED_CONTENT_ENCODINGS:
                raise PresignValidationError("unsupported content_encoding")

        params: dict[str, str] = {
            "Bucket": self.bucket,
            "Key": key,
            "ContentType": normalized_content_type,
        }
        headers = {"Content-Type": normalized_content_type}
        if normalized_encoding is not None:
            params["ContentEncoding"] = normalized_encoding
            headers["Content-Encoding"] = normalized_encoding

        try:
            client = self.client_factory()
            url = client.generate_presigned_url(
                "put_object",
                Params=params,
                ExpiresIn=self.expires_in,
                HttpMethod="PUT",
            )
        except Exception as exc:
            raise PresignGenerationError(
                "failed to generate S3 upload URL"
            ) from exc
        if not isinstance(url, str) or not url:
            raise PresignGenerationError("S3 client returned an empty upload URL")
        return url, headers

    def validate_key(self, key: str) -> None:
        if not key or key != key.strip():
            raise PresignValidationError("key must not be empty or padded")
        if key.startswith(("/", "\\")) or "\\" in key:
            raise PresignValidationError("absolute paths and backslashes are forbidden")
        if ".." in key:
            raise PresignValidationError("key must not contain '..'")
        if any(ord(character) < 32 or ord(character) == 127 for character in key):
            raise PresignValidationError("key must not contain control characters")
        segments = key.split("/")
        if any(segment in {"", "."} for segment in segments):
            raise PresignValidationError("key contains an invalid path segment")
        if not any(key.startswith(prefix) for prefix in self.allowed_prefixes):
            raise PresignValidationError("key prefix is not allowed")

    def _build_client(self) -> Any:
        # Client creation is intentionally deferred until an API request so a
        # credential-less local process can still start and serve other APIs.
        return boto3.client("s3", region_name=self.region)

    @staticmethod
    def _normalize_prefix(prefix: str) -> str:
        normalized = str(prefix).strip().strip("/")
        if not normalized or ".." in normalized or "\\" in normalized:
            raise ValueError("invalid S3 presign prefix")
        return f"{normalized}/"
