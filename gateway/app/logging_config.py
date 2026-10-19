"""Structured JSON logging to stdout."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone


SUPPORTED_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
STANDARD_RECORD_FIELDS = set(logging.makeLogRecord({}).__dict__) | {
    "message",
    "asctime",
}


class JsonLogFormatter(logging.Formatter):
    """Render structured logs as JSON and flagged console banners verbatim."""

    def format(self, record: logging.LogRecord) -> str:
        if getattr(record, "raw_console", False):
            return record.getMessage()

        timestamp = (
            datetime.fromtimestamp(record.created, tz=timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
        value: dict[str, object] = {
            "ts": timestamp,
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", record.getMessage()),
        }
        for key, item in record.__dict__.items():
            if key not in STANDARD_RECORD_FIELDS and key not in value:
                value[key] = item
        if record.exc_info:
            value["exception"] = self.formatException(record.exc_info)
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def configure_logging(level: str = "INFO") -> None:
    """Replace root handlers with one stdout JSON handler."""

    normalized = level.strip().upper()
    if normalized not in SUPPORTED_LOG_LEVELS:
        allowed = ", ".join(sorted(SUPPORTED_LOG_LEVELS))
        raise ValueError(f"log level must be one of {allowed}")

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter())
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(normalized)
