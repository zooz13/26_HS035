"""Safe resend commands for persisted setup finish payloads."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

from app.baseline_state import load_baseline_state, save_baseline_state
from app.config import ConfigurationError, GatewayConfig
from app.logging_config import configure_logging
from app.setup_api_client import SetupApiClient, SetupApiError
from app.setup_spool import SetupSpoolStore
from app.setup_state import SetupStateStore


LOGGER = logging.getLogger(__name__)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Gateway setup payload tools")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("baseline-finish", "calibration-finish"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--payload", required=True, type=Path)
    subparsers.add_parser("retry-pending")
    return parser


def _client_for(
    envelope: dict[str, Any],
    config: GatewayConfig,
) -> SetupApiClient:
    return SetupApiClient(
        str(envelope.get("server_url") or config.model_server_url),
        config.model_server_timeout_sec,
    )


def _update_setup_state(
    config: GatewayConfig,
    envelope: dict[str, Any],
    response: dict[str, Any],
) -> None:
    store = SetupStateStore(config.local_setup_dir)
    state = store.load(str(envelope["room_id"]))
    if envelope["operation"] == "baseline_finish":
        candidate_path = envelope.get("candidate_baseline_state_path")
        if candidate_path:
            candidate = load_baseline_state(str(candidate_path))
            expected_baseline_id = str(
                envelope["payload"]["baseline_id"]
            )
            if candidate.baseline_id != expected_baseline_id:
                raise ValueError(
                    "candidate baseline_id does not match finish payload: "
                    f"{candidate.baseline_id!r} != "
                    f"{expected_baseline_id!r}"
                )
            save_baseline_state(config.baseline_state_json, candidate)
    if state is None:
        return
    if envelope["operation"] == "baseline_finish":
        baseline_id = str(envelope["payload"]["baseline_id"])
        calibration_id = response.get("calibration_id")
        server_status = response.get("status")
        store.save(
            state.updated(
                baseline_id=baseline_id,
                calibration_id=(
                    str(calibration_id) if calibration_id else None
                ),
                setup_status=(
                    "ready"
                    if calibration_id
                    and server_status in {"ready", "running"}
                    else "baseline_ready"
                ),
            )
        )
    elif envelope["operation"] == "calibration_finish":
        calibration_id = response.get("calibration_id")
        if calibration_id:
            store.save(
                state.updated(
                    calibration_id=str(calibration_id),
                    setup_status="ready",
                )
            )


def resend_payload(
    path: Path,
    config: GatewayConfig,
    spool_store: SetupSpoolStore,
) -> int:
    envelope = json.loads(path.read_text(encoding="utf-8"))
    try:
        response = _client_for(envelope, config).send_finish_envelope(envelope)
    except (SetupApiError, OSError, ValueError) as exc:
        spool_store.create(
            envelope=envelope,
            source_payload_path=path,
            last_error=str(exc),
        )
        LOGGER.error(
            "setup finish resend failed",
            extra={
                "event": "setup_finish_resend_failed",
                "operation": envelope.get("operation"),
                "room_id": envelope.get("room_id"),
                "payload_path": str(path),
                "error": str(exc),
            },
        )
        return 1
    _update_setup_state(config, envelope, response)
    LOGGER.info(
        "setup finish resend completed",
        extra={
            "event": "setup_finish_resend_completed",
            "operation": envelope.get("operation"),
            "room_id": envelope.get("room_id"),
            "payload_path": str(path),
        },
    )
    return 0


def retry_pending(
    config: GatewayConfig,
    spool_store: SetupSpoolStore,
) -> int:
    failed = 0
    for manifest in spool_store.list_pending():
        envelope = spool_store.load_envelope(manifest)
        try:
            response = _client_for(
                envelope,
                config,
            ).send_finish_envelope(envelope)
            _update_setup_state(config, envelope, response)
            spool_store.mark_sent(manifest)
        except (SetupApiError, OSError, ValueError) as exc:
            spool_store.record_failure(manifest, str(exc))
            failed += 1
    LOGGER.info(
        "setup spool retry completed",
        extra={
            "event": "setup_spool_retry_completed",
            "failed_count": failed,
        },
    )
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        config = GatewayConfig.from_env()
    except ConfigurationError as exc:
        configure_logging("INFO")
        LOGGER.error(
            "configuration invalid",
            extra={"event": "config_invalid", "error": str(exc)},
        )
        return 1
    configure_logging(config.log_level)
    spool_store = SetupSpoolStore(config.local_setup_spool_dir)
    if args.command == "retry-pending":
        return retry_pending(config, spool_store)
    expected_operation = args.command.replace("-", "_")
    envelope = json.loads(args.payload.read_text(encoding="utf-8"))
    if envelope.get("operation") != expected_operation:
        LOGGER.error(
            "setup payload operation mismatch",
            extra={
                "event": "setup_payload_invalid",
                "expected_operation": expected_operation,
                "actual_operation": envelope.get("operation"),
            },
        )
        return 1
    return resend_payload(args.payload, config, spool_store)


if __name__ == "__main__":
    raise SystemExit(main())
