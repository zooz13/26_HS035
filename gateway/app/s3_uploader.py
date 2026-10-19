"""S3 upload orchestration with dry-run and persistent spool support."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse

from app.jsonl_writer import WrittenWindow
from app.models import SingleObjectSpoolManifest, SpoolManifest
from app.preprocessed_log import PreprocessedArtifact
from app.spool import SpoolStore


LOGGER = logging.getLogger(__name__)


class PresignedUploadError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class UploadResult:
    success: bool
    dry_run: bool
    data_uploaded: bool
    metadata_uploaded: bool
    spool_manifest: SpoolManifest | SingleObjectSpoolManifest | None = None
    error: str | None = None


class S3Uploader:
    """Upload data before metadata and preserve failures in a local spool."""

    def __init__(
        self,
        *,
        s3_upload_enabled: bool,
        delete_local_after_upload: bool,
        spool_store: SpoolStore,
        model_server_url: str,
        timeout_sec: float,
        opener: Callable[..., Any] = urlrequest.urlopen,
        status_callback: Callable[..., None] | None = None,
    ) -> None:
        self.s3_upload_enabled = s3_upload_enabled
        self.delete_local_after_upload = delete_local_after_upload
        self.spool_store = spool_store
        self.model_server_url = model_server_url.rstrip("/")
        self.timeout_sec = timeout_sec
        self.opener = opener
        self.status_callback = status_callback

    @property
    def presign_endpoint(self) -> str:
        return f"{self.model_server_url}/api/s3/presign-upload"

    def upload(self, window: WrittenWindow) -> UploadResult:
        if not self.s3_upload_enabled:
            LOGGER.info(
                "S3 dry-run: data_path=%s metadata_path=%s bucket=%s "
                "data_key=%s metadata_key=%s payload_type=%s "
                "frame_count=%s size_bytes=%s sha256=%s",
                window.data_path,
                window.metadata_path,
                window.metadata.s3_bucket,
                window.s3_data_key,
                window.s3_metadata_key,
                window.metadata.type.value,
                window.metadata.frame_count,
                window.metadata.size_bytes,
                window.metadata.sha256,
            )
            return UploadResult(
                success=True,
                dry_run=True,
                data_uploaded=False,
                metadata_uploaded=False,
            )

        data_uploaded = False
        try:
            self._upload_data(
                window.data_path,
                window.metadata.s3_bucket,
                window.s3_data_key,
            )
            data_uploaded = True
            self._upload_metadata(
                window.metadata_path,
                window.metadata.s3_bucket,
                window.s3_metadata_key,
            )
        except Exception as exc:
            manifest = self.spool_store.create(
                window,
                data_uploaded=data_uploaded,
                metadata_uploaded=False,
                last_error=str(exc),
            )
            if data_uploaded and self.delete_local_after_upload:
                self._delete_if_exists(window.data_path)
                self._delete_if_exists(window.jsonl_path)
            self._notify(
                "s3_upload_result",
                success=False,
                error=str(exc),
                payload_type=window.metadata.type.value,
            )
            return UploadResult(
                success=False,
                dry_run=False,
                data_uploaded=data_uploaded,
                metadata_uploaded=False,
                spool_manifest=manifest,
                error=str(exc),
            )

        if self.delete_local_after_upload:
            self._delete_window_files(window)
        result = UploadResult(
            success=True,
            dry_run=False,
            data_uploaded=True,
            metadata_uploaded=True,
        )
        self._notify(
            "s3_upload_result",
            success=True,
            error=None,
            payload_type=window.metadata.type.value,
        )
        return result

    def retry_pending(self) -> list[UploadResult]:
        if not self.s3_upload_enabled:
            return []

        results: list[UploadResult] = []
        for manifest in self.spool_store.list_pending():
            current = manifest
            try:
                if not current.data_uploaded:
                    if current.spool_data_path is None:
                        raise FileNotFoundError("spool data path is missing")
                    self._upload_data(
                        current.spool_data_path,
                        current.s3_bucket,
                        current.s3_data_key,
                    )
                    current = self.spool_store.update(
                        current,
                        data_uploaded=True,
                        last_error=None,
                    )
                    self._delete_if_exists(current.spool_data_path)
                    current = self.spool_store.update(
                        current,
                        spool_data_path=None,
                    )

                if not current.metadata_uploaded:
                    if current.spool_metadata_path is None:
                        raise FileNotFoundError(
                            "spool metadata path is missing"
                        )
                    self._upload_metadata(
                        current.spool_metadata_path,
                        current.s3_bucket,
                        current.s3_metadata_key,
                    )
                    current = self.spool_store.update(
                        current,
                        metadata_uploaded=True,
                        last_error=None,
                    )
                    self._delete_if_exists(current.spool_metadata_path)
                    current = self.spool_store.update(
                        current,
                        spool_metadata_path=None,
                    )

                self.spool_store.mark_sent(current)
                results.append(
                    UploadResult(
                        success=True,
                        dry_run=False,
                        data_uploaded=True,
                        metadata_uploaded=True,
                    )
                )
                self._notify(
                    "s3_upload_result",
                    success=True,
                    error=None,
                    payload_type=current.payload_type.value,
                )
            except Exception as exc:
                current = self.spool_store.record_retry_failure(
                    current,
                    str(exc),
                )
                results.append(
                    UploadResult(
                        success=False,
                        dry_run=False,
                        data_uploaded=current.data_uploaded,
                        metadata_uploaded=current.metadata_uploaded,
                        spool_manifest=current,
                        error=str(exc),
                    )
                )
                self._notify(
                    "s3_upload_result",
                    success=False,
                    error=str(exc),
                    payload_type=current.payload_type.value,
                )
        for manifest in self.spool_store.list_pending_single():
            try:
                self._upload_preprocessed_file(
                    manifest.spool_file_path,
                    manifest.s3_bucket,
                    manifest.s3_key,
                )
                self.spool_store.mark_single_sent(manifest)
                results.append(
                    UploadResult(
                        success=True,
                        dry_run=False,
                        data_uploaded=True,
                        metadata_uploaded=True,
                    )
                )
                self._notify(
                    "s3_upload_result",
                    success=True,
                    error=None,
                    payload_type=manifest.payload_type.value,
                )
            except Exception as exc:
                updated = self.spool_store.record_single_retry_failure(
                    manifest,
                    str(exc),
                )
                results.append(
                    UploadResult(
                        success=False,
                        dry_run=False,
                        data_uploaded=False,
                        metadata_uploaded=False,
                        error=str(exc),
                    )
                )
                self._notify(
                    "s3_upload_result",
                    success=False,
                    error=str(exc),
                    payload_type=updated.payload_type.value,
                )
        return results

    def upload_preprocessed(
        self,
        artifact: PreprocessedArtifact,
    ) -> UploadResult:
        if not self.s3_upload_enabled:
            LOGGER.info(
                "preprocessed S3 dry-run",
                extra={
                    "event": "preprocessed_s3_dry_run",
                    "local_path": str(artifact.local_path),
                    "bucket": artifact.s3_bucket,
                    "s3_key": artifact.s3_key,
                    "window_id": artifact.window_id,
                },
            )
            return UploadResult(
                success=True,
                dry_run=True,
                data_uploaded=False,
                metadata_uploaded=False,
            )
        try:
            self._upload_preprocessed_file(
                artifact.local_path,
                artifact.s3_bucket,
                artifact.s3_key,
            )
        except Exception as exc:
            manifest = self.spool_store.create_single(
                artifact,
                last_error=str(exc),
            )
            self._notify(
                "s3_upload_result",
                success=False,
                error=str(exc),
                payload_type="preprocessed_payload",
            )
            return UploadResult(
                success=False,
                dry_run=False,
                data_uploaded=False,
                metadata_uploaded=False,
                spool_manifest=manifest,
                error=str(exc),
            )
        if self.delete_local_after_upload:
            self._delete_if_exists(artifact.local_path)
        result = UploadResult(
            success=True,
            dry_run=False,
            data_uploaded=True,
            metadata_uploaded=True,
        )
        self._notify(
            "s3_upload_result",
            success=True,
            error=None,
            payload_type="preprocessed_payload",
        )
        return result

    def _upload_data(self, path: Path, _bucket: str, key: str) -> None:
        self._upload_via_presigned_url(
            path,
            key,
            content_type="application/x-ndjson",
            content_encoding="gzip",
        )

    def _upload_metadata(self, path: Path, _bucket: str, key: str) -> None:
        self._upload_via_presigned_url(
            path,
            key,
            content_type="application/json",
        )

    def _upload_preprocessed_file(
        self,
        path: Path,
        _bucket: str,
        key: str,
    ) -> None:
        self._upload_via_presigned_url(
            path,
            key,
            content_type="application/json",
            content_encoding="gzip",
        )

    def _upload_via_presigned_url(
        self,
        path: Path,
        key: str,
        *,
        content_type: str,
        content_encoding: str | None = None,
    ) -> None:
        url, headers = self._request_presigned_url(
            key=key,
            content_type=content_type,
            content_encoding=content_encoding,
        )
        upload_request = urlrequest.Request(
            url,
            data=path.read_bytes(),
            method="PUT",
            headers=headers,
        )
        try:
            with self.opener(
                upload_request,
                timeout=self.timeout_sec,
            ) as response:
                status_code = int(response.getcode())
                response.read()
        except HTTPError as exc:
            raise PresignedUploadError(
                f"S3 presigned PUT failed: HTTP {exc.code}"
            ) from exc
        except (URLError, OSError) as exc:
            raise PresignedUploadError("S3 presigned PUT failed") from exc
        if not 200 <= status_code < 300:
            raise PresignedUploadError(
                f"S3 presigned PUT failed: HTTP {status_code}"
            )
        LOGGER.info(
            "%s [ S3 UPLOAD SUCCESS ] %s\n"
            "key=%s | status=%s | type=%s | encoding=%s\n"
            "%s",
            "\\" * 20,
            "/" * 20,
            key,
            status_code,
            content_type,
            content_encoding or "none",
            "/" * 63,
            extra={
                "raw_console": True,
                "event": "s3_upload_success",
                "s3_key": key,
                "status_code": status_code,
                "content_type": content_type,
                "content_encoding": content_encoding,
                "local_path": str(path),
            },
        )

    def _request_presigned_url(
        self,
        *,
        key: str,
        content_type: str,
        content_encoding: str | None,
    ) -> tuple[str, dict[str, str]]:
        payload: dict[str, str] = {
            "key": key,
            "content_type": content_type,
        }
        if content_encoding is not None:
            payload["content_encoding"] = content_encoding
        request = urlrequest.Request(
            self.presign_endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with self.opener(request, timeout=self.timeout_sec) as response:
                status_code = int(response.getcode())
                body = response.read()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise PresignedUploadError(
                f"presign request failed: HTTP {exc.code}: {detail}"
            ) from exc
        except (URLError, OSError) as exc:
            raise PresignedUploadError("presign request failed") from exc
        if not 200 <= status_code < 300:
            raise PresignedUploadError(
                f"presign request failed: HTTP {status_code}"
            )
        try:
            value = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PresignedUploadError(
                "presign response is not valid JSON"
            ) from exc
        if not isinstance(value, dict) or value.get("method") != "PUT":
            raise PresignedUploadError("presign response method must be PUT")
        url = value.get("url")
        parsed_url = urlparse(url) if isinstance(url, str) else None
        if (
            parsed_url is None
            or parsed_url.scheme not in {"http", "https"}
            or not parsed_url.netloc
        ):
            raise PresignedUploadError("presign response URL is invalid")
        raw_headers = value.get("headers")
        if not isinstance(raw_headers, dict) or not all(
            isinstance(name, str) and isinstance(header_value, str)
            for name, header_value in raw_headers.items()
        ):
            raise PresignedUploadError("presign response headers are invalid")
        headers = dict(raw_headers)
        if headers.get("Content-Type") != content_type:
            raise PresignedUploadError(
                "presign response Content-Type does not match the request"
            )
        if content_encoding is not None and (
            headers.get("Content-Encoding") != content_encoding
        ):
            raise PresignedUploadError(
                "presign response Content-Encoding does not match the request"
            )
        return url, headers

    @staticmethod
    def _delete_window_files(window: WrittenWindow) -> None:
        S3Uploader._delete_if_exists(window.jsonl_path)
        S3Uploader._delete_if_exists(window.data_path)
        S3Uploader._delete_if_exists(window.metadata_path)

    @staticmethod
    def _delete_if_exists(path: Path | None) -> None:
        if path is not None:
            path.unlink(missing_ok=True)

    def _notify(self, event: str, **values: Any) -> None:
        if self.status_callback is None:
            return
        try:
            self.status_callback(event, **values)
        except Exception:
            LOGGER.warning(
                "S3 status callback failed",
                extra={
                    "event": "status_callback_failed",
                    "source": "s3_uploader",
                    "status_event": event,
                },
            )
