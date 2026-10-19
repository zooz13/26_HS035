"""Environment-backed gateway configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv


REQUIRED_AWS_REGION = "us-west-2"
SUPPORTED_COMPRESSION = {"gzip"}
SUPPORTED_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


class ConfigurationError(ValueError):
    """Raised when gateway configuration is missing or invalid."""


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigurationError(f"{name} is required")
    return value


def _as_bool(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default

    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(
        f"{name} must be one of true/false, 1/0, yes/no, or on/off"
    )


def _as_positive_int(name: str, default: int) -> int:
    raw_value = os.getenv(name, str(default))
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ConfigurationError(f"{name} must be greater than zero")
    return value


def _as_int_in_range(name: str, default: int, minimum: int, maximum: int) -> int:
    raw_value = os.getenv(name, str(default))
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ConfigurationError(
            f"{name} must be between {minimum} and {maximum}"
        )
    return value


def _as_float_at_least(name: str, default: float, minimum: float) -> float:
    raw_value = os.getenv(name, str(default))
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a number") from exc
    if value < minimum:
        raise ConfigurationError(f"{name} must be at least {minimum}")
    return value


def _as_positive_float(name: str, default: float) -> float:
    raw_value = os.getenv(name, str(default))
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a number") from exc
    if value <= 0:
        raise ConfigurationError(f"{name} must be greater than zero")
    return value


def _prefix(name: str, default: str) -> str:
    value = os.getenv(name, default).strip().strip("/")
    if not value:
        raise ConfigurationError(f"{name} must not be empty")
    return f"{value}/"


def _http_url(name: str, default: str) -> str:
    value = os.getenv(name, default).strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigurationError(
            f"{name} must be an absolute http or https URL"
        )
    return value


def _optional_text(name: str, default: str) -> str:
    value = os.getenv(name, default).strip()
    if not value:
        raise ConfigurationError(f"{name} must not be empty")
    return value


@dataclass(frozen=True, slots=True)
class GatewayConfig:
    """Validated runtime configuration for the gateway."""

    gateway_id: str
    device_id: str
    link_id: str
    serial_port: str
    serial_baudrate: int
    serial_timeout_sec: float
    serial_reconnect_sec: float
    serial_max_line_bytes: int
    serial_error_capture_bytes: int
    log_level: str
    realtime_window_sec: int
    raw_log_window_sec: int
    local_data_dir: Path
    local_rawlog_dir: Path
    local_spool_dir: Path
    local_failed_dir: Path
    local_realtime_dryrun_dir: Path
    local_realtime_spool_dir: Path
    local_preprocessed_log_dir: Path
    local_setup_dir: Path
    local_setup_spool_dir: Path
    compression: str
    gzip_compress_level: int
    delete_uncompressed_after_compress: bool
    s3_upload_enabled: bool
    delete_local_after_upload: bool
    aws_region: str
    s3_bucket: str
    s3_rawlog_prefix: str
    s3_preprocessed_prefix: str
    model_server_url: str
    model_server_timeout_sec: float
    realtime_send_enabled: bool
    realtime_retry_sec: float
    baseline_state_json: Path
    baseline_reload_sec: float
    baseline_id: str
    calibration_id: str | None
    device_group_id: str
    room_id: str
    room_name: str
    preprocessed_log_enabled: bool
    setup_reload_sec: float
    amplitude_npy_enabled: bool
    transport_test_enabled: bool
    transport_test_stage1_score: float
    dashboard_control_enabled: bool
    remote_setup_poll_sec: float
    baseline_collection_sec: float
    dashboard_status_enabled: bool
    dashboard_status_path: Path
    dashboard_update_sec: float
    dashboard_http_enabled: bool
    dashboard_http_host: str
    dashboard_http_port: int
    dashboard_dir: Path

    @classmethod
    def from_env(cls, env_file: str | Path | None = None) -> "GatewayConfig":
        """Load `.env` values, then validate the effective environment."""

        load_dotenv(dotenv_path=env_file, override=False)

        aws_region = os.getenv("AWS_REGION", REQUIRED_AWS_REGION).strip()
        if aws_region != REQUIRED_AWS_REGION:
            raise ConfigurationError(
                f"AWS_REGION must be {REQUIRED_AWS_REGION}; got {aws_region!r}"
            )

        compression = os.getenv("COMPRESSION", "gzip").strip().lower()
        if compression not in SUPPORTED_COMPRESSION:
            raise ConfigurationError(
                "COMPRESSION must be 'gzip'; other codecs are not implemented yet"
            )

        log_level = os.getenv("LOG_LEVEL", "INFO").strip().upper()
        if log_level not in SUPPORTED_LOG_LEVELS:
            allowed = ", ".join(sorted(SUPPORTED_LOG_LEVELS))
            raise ConfigurationError(
                f"LOG_LEVEL must be one of {allowed}; got {log_level!r}"
            )

        return cls(
            gateway_id=_required("GATEWAY_ID"),
            device_id=_required("DEVICE_ID"),
            link_id=_required("LINK_ID"),
            serial_port=_required("SERIAL_PORT"),
            serial_baudrate=_as_positive_int("SERIAL_BAUDRATE", 921600),
            serial_timeout_sec=_as_positive_float("SERIAL_TIMEOUT_SEC", 1.0),
            serial_reconnect_sec=_as_float_at_least(
                "SERIAL_RECONNECT_SEC", 5.0, 1.0
            ),
            serial_max_line_bytes=_as_positive_int(
                "SERIAL_MAX_LINE_BYTES", 16384
            ),
            serial_error_capture_bytes=_as_int_in_range(
                "SERIAL_ERROR_CAPTURE_BYTES", 512, 64, 4096
            ),
            log_level=log_level,
            realtime_window_sec=_as_positive_int("REALTIME_WINDOW_SEC", 5),
            raw_log_window_sec=_as_positive_int("RAW_LOG_WINDOW_SEC", 60),
            local_data_dir=Path(os.getenv("LOCAL_DATA_DIR", "./data")),
            local_rawlog_dir=Path(
                os.getenv("LOCAL_RAWLOG_DIR", "./data/rawlogs")
            ),
            local_spool_dir=Path(os.getenv("LOCAL_SPOOL_DIR", "./data/spool")),
            local_failed_dir=Path(os.getenv("LOCAL_FAILED_DIR", "./data/failed")),
            local_realtime_dryrun_dir=Path(
                os.getenv(
                    "LOCAL_REALTIME_DRYRUN_DIR",
                    "./data/realtime_dryrun",
                )
            ),
            local_realtime_spool_dir=Path(
                os.getenv(
                    "LOCAL_REALTIME_SPOOL_DIR",
                    "./data/realtime_spool",
                )
            ),
            local_preprocessed_log_dir=Path(
                os.getenv(
                    "LOCAL_PREPROCESSED_LOG_DIR",
                    "./data/preprocessed",
                )
            ),
            local_setup_dir=Path(
                os.getenv("LOCAL_SETUP_DIR", "./data/setup")
            ),
            local_setup_spool_dir=Path(
                os.getenv(
                    "LOCAL_SETUP_SPOOL_DIR",
                    "./data/setup_spool",
                )
            ),
            compression=compression,
            gzip_compress_level=_as_int_in_range(
                "GZIP_COMPRESS_LEVEL", 6, 1, 9
            ),
            delete_uncompressed_after_compress=_as_bool(
                "DELETE_UNCOMPRESSED_AFTER_COMPRESS", True
            ),
            s3_upload_enabled=_as_bool("S3_UPLOAD_ENABLED", False),
            delete_local_after_upload=_as_bool(
                "DELETE_LOCAL_AFTER_UPLOAD", True
            ),
            aws_region=aws_region,
            s3_bucket=_required("S3_BUCKET"),
            s3_rawlog_prefix=_prefix("S3_RAWLOG_PREFIX", "rawlog/"),
            s3_preprocessed_prefix=_prefix(
                "S3_PREPROCESSED_PREFIX",
                "preprocessed/",
            ),
            model_server_url=_http_url(
                "MODEL_SERVER_URL",
                "http://127.0.0.1:8000",
            ),
            model_server_timeout_sec=_as_positive_float(
                "MODEL_SERVER_TIMEOUT_SEC",
                20.0,
            ),
            realtime_send_enabled=_as_bool(
                "REALTIME_SEND_ENABLED",
                False,
            ),
            realtime_retry_sec=_as_positive_float(
                "REALTIME_RETRY_SEC",
                10.0,
            ),
            baseline_state_json=Path(
                os.getenv(
                    "BASELINE_STATE_JSON",
                    "./data/baseline/baseline_state.json",
                )
            ),
            baseline_reload_sec=_as_positive_float(
                "BASELINE_RELOAD_SEC",
                10.0,
            ),
            baseline_id=_required("BASELINE_ID"),
            calibration_id=(
                os.getenv("CALIBRATION_ID", "").strip() or None
            ),
            device_group_id=_required("DEVICE_GROUP_ID"),
            room_id=_required("ROOM_ID"),
            room_name=_optional_text("ROOM_NAME", _required("ROOM_ID")),
            preprocessed_log_enabled=_as_bool(
                "PREPROCESSED_LOG_ENABLED",
                True,
            ),
            setup_reload_sec=_as_positive_float(
                "SETUP_RELOAD_SEC",
                10.0,
            ),
            amplitude_npy_enabled=_as_bool(
                "AMPLITUDE_NPY_ENABLED",
                False,
            ),
            transport_test_enabled=_as_bool(
                "TRANSPORT_TEST_ENABLED",
                False,
            ),
            transport_test_stage1_score=_as_float_at_least(
                "TRANSPORT_TEST_STAGE1_SCORE",
                3.0,
                0.0,
            ),
            dashboard_control_enabled=_as_bool(
                "DASHBOARD_CONTROL_ENABLED",
                False,
            ),
            remote_setup_poll_sec=_as_positive_float(
                "REMOTE_SETUP_POLL_SEC",
                2.0,
            ),
            baseline_collection_sec=_as_positive_float(
                "BASELINE_COLLECTION_SEC",
                300.0,
            ),
            dashboard_status_enabled=_as_bool(
                "DASHBOARD_STATUS_ENABLED",
                True,
            ),
            dashboard_status_path=Path(
                os.getenv(
                    "DASHBOARD_STATUS_PATH",
                    "./dashboard/status.json",
                )
            ),
            dashboard_update_sec=_as_positive_float(
                "DASHBOARD_UPDATE_SEC",
                1.0,
            ),
            dashboard_http_enabled=_as_bool(
                "DASHBOARD_HTTP_ENABLED",
                False,
            ),
            dashboard_http_host=_optional_text(
                "DASHBOARD_HTTP_HOST",
                "127.0.0.1",
            ),
            dashboard_http_port=_as_int_in_range(
                "DASHBOARD_HTTP_PORT",
                8080,
                1,
                65535,
            ),
            dashboard_dir=Path(
                os.getenv("DASHBOARD_DIR", "./dashboard")
            ),
        )
