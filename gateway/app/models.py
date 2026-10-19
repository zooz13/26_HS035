"""Shared data contracts for parsed CSI frames and persisted payloads."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "1.0"


def utc_isoformat(value: datetime) -> str:
    """Serialize an aware datetime as UTC with a `Z` suffix."""

    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def epoch_utc_isoformat(value: float) -> str:
    """Serialize Unix epoch seconds as an ISO 8601 UTC string."""

    return utc_isoformat(datetime.fromtimestamp(value, tz=timezone.utc))


class PayloadType(str, Enum):
    REALTIME_WINDOW = "realtime_window"
    RAWLOG_1M = "rawlog_1m"
    PREPROCESSED_PAYLOAD = "preprocessed_payload"


@dataclass(frozen=True, slots=True)
class CanonicalCsiFrame:
    schema_version: str
    type: str
    gateway_id: str
    device_id: str
    link_id: str
    gateway_ts: float
    id: int
    mac: str
    rssi: int
    rate: int
    sig_mode: int
    mcs: int
    bandwidth: int
    smoothing: int
    not_sounding: int
    aggregation: int
    stbc: int
    fec_coding: int
    sgi: int
    noise_floor: int
    ampdu_cnt: int
    channel: int
    secondary_channel: int
    local_timestamp: int
    ant: int
    sig_len: int
    rx_format: int
    len: int
    first_word_invalid: int
    csi_format: str
    data: tuple[int, ...]
    raw_line: str
    fft_gain: int | None = None
    agc_gain: int | None = None
    compensate_gain_x1000000: int | None = None

    def to_dict(self, *, include_raw_line: bool = False) -> dict[str, Any]:
        """Serialize an inference record or a rawlog record."""

        result = asdict(self)
        result["data"] = list(self.data)
        if not include_raw_line:
            result.pop("raw_line")
        return {
            key: value
            for key, value in result.items()
            if value is not None
        }


@dataclass(frozen=True, slots=True)
class ParseErrorRecord:
    schema_version: str
    type: str
    gateway_id: str
    device_id: str
    link_id: str
    gateway_ts: float
    error: str
    raw_line: str | None = None
    raw_bytes_hex: str | None = None
    raw_line_truncated: str | None = None
    raw_bytes_hex_truncated: str | None = None
    exceeded_max_line_bytes: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in asdict(self).items()
            if value is not None
        }


@dataclass(frozen=True, slots=True)
class WindowMetadata:
    type: PayloadType
    schema_version: str
    gateway_id: str
    device_id: str
    link_id: str
    window_start: float
    window_end: float
    window_start_iso: str
    window_end_iso: str
    window_sec: int
    frame_count: int
    estimated_fps: float
    s3_bucket: str
    s3_key: str
    metadata_s3_key: str
    size_bytes: int
    sha256: str
    rssi_min: int | None
    rssi_max: int | None
    rssi_avg: float | None
    noise_floor_avg: float | None
    parse_error_count: int

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["type"] = self.type.value
        return result


@dataclass(frozen=True, slots=True)
class SpoolManifest:
    spool_id: str
    original_data_path: Path
    original_metadata_path: Path
    spool_data_path: Path | None
    spool_metadata_path: Path | None
    s3_bucket: str
    s3_data_key: str
    s3_metadata_key: str
    payload_type: PayloadType
    data_uploaded: bool
    metadata_uploaded: bool
    created_at: datetime
    retry_count: int
    last_error: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "spool_id": self.spool_id,
            "payload_type": self.payload_type.value,
            "original_data_path": str(self.original_data_path),
            "original_metadata_path": str(self.original_metadata_path),
            "spool_data_path": (
                str(self.spool_data_path)
                if self.spool_data_path is not None
                else None
            ),
            "spool_metadata_path": (
                str(self.spool_metadata_path)
                if self.spool_metadata_path is not None
                else None
            ),
            "s3_bucket": self.s3_bucket,
            "s3_data_key": self.s3_data_key,
            "s3_metadata_key": self.s3_metadata_key,
            "data_uploaded": self.data_uploaded,
            "metadata_uploaded": self.metadata_uploaded,
            "created_at": utc_isoformat(self.created_at),
            "retry_count": self.retry_count,
            "last_error": self.last_error,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SpoolManifest":
        created_at = datetime.fromisoformat(
            str(value["created_at"]).replace("Z", "+00:00")
        )
        return cls(
            spool_id=str(value["spool_id"]),
            payload_type=PayloadType(str(value["payload_type"])),
            original_data_path=Path(str(value["original_data_path"])),
            original_metadata_path=Path(
                str(value["original_metadata_path"])
            ),
            spool_data_path=(
                Path(str(value["spool_data_path"]))
                if value.get("spool_data_path")
                else None
            ),
            spool_metadata_path=(
                Path(str(value["spool_metadata_path"]))
                if value.get("spool_metadata_path")
                else None
            ),
            s3_bucket=str(value["s3_bucket"]),
            s3_data_key=str(value["s3_data_key"]),
            s3_metadata_key=str(value["s3_metadata_key"]),
            data_uploaded=bool(value["data_uploaded"]),
            metadata_uploaded=bool(value["metadata_uploaded"]),
            created_at=created_at,
            retry_count=int(value["retry_count"]),
            last_error=(
                str(value["last_error"])
                if value.get("last_error") is not None
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class SingleObjectSpoolManifest:
    spool_id: str
    payload_type: PayloadType
    local_file_path: Path
    spool_file_path: Path
    s3_bucket: str
    s3_key: str
    created_at: datetime
    retry_count: int
    last_error: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "spool_id": self.spool_id,
            "payload_type": self.payload_type.value,
            "local_file_path": str(self.local_file_path),
            "spool_file_path": str(self.spool_file_path),
            "s3_bucket": self.s3_bucket,
            "s3_key": self.s3_key,
            "created_at": utc_isoformat(self.created_at),
            "retry_count": self.retry_count,
            "last_error": self.last_error,
        }

    @classmethod
    def from_dict(
        cls,
        value: dict[str, Any],
    ) -> "SingleObjectSpoolManifest":
        return cls(
            spool_id=str(value["spool_id"]),
            payload_type=PayloadType(str(value["payload_type"])),
            local_file_path=Path(str(value["local_file_path"])),
            spool_file_path=Path(str(value["spool_file_path"])),
            s3_bucket=str(value["s3_bucket"]),
            s3_key=str(value["s3_key"]),
            created_at=datetime.fromisoformat(
                str(value["created_at"]).replace("Z", "+00:00")
            ),
            retry_count=int(value["retry_count"]),
            last_error=(
                str(value["last_error"])
                if value.get("last_error") is not None
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class RealtimeSpoolManifest:
    spool_id: str
    room_id: str
    window_id: str
    endpoint: str
    local_payload_path: Path
    created_at: datetime
    retry_count: int
    last_error: str | None
    last_attempt_at: datetime | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "spool_id": self.spool_id,
            "room_id": self.room_id,
            "window_id": self.window_id,
            "endpoint": self.endpoint,
            "local_payload_path": str(self.local_payload_path),
            "created_at": utc_isoformat(self.created_at),
            "retry_count": self.retry_count,
            "last_error": self.last_error,
            "last_attempt_at": (
                utc_isoformat(self.last_attempt_at)
                if self.last_attempt_at is not None
                else None
            ),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "RealtimeSpoolManifest":
        last_attempt = value.get("last_attempt_at")
        return cls(
            spool_id=str(value["spool_id"]),
            room_id=str(value["room_id"]),
            window_id=str(value["window_id"]),
            endpoint=str(value["endpoint"]),
            local_payload_path=Path(str(value["local_payload_path"])),
            created_at=datetime.fromisoformat(
                str(value["created_at"]).replace("Z", "+00:00")
            ),
            retry_count=int(value["retry_count"]),
            last_error=(
                str(value["last_error"])
                if value.get("last_error") is not None
                else None
            ),
            last_attempt_at=(
                datetime.fromisoformat(str(last_attempt).replace("Z", "+00:00"))
                if last_attempt
                else None
            ),
        )
