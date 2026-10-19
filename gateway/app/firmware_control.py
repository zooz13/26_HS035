"""Runtime control of the CSI TX through the RX UART bridge."""

from __future__ import annotations

import math
import threading
import time
from typing import Any, Callable


CSI_CTRL_PREFIX = "CSI_CTRL"
DEFAULT_MIN_RATE_HZ = 1
DEFAULT_MAX_RATE_HZ = 200
DEFAULT_MIN_TX_POWER_QDBM = 8
DEFAULT_MAX_TX_POWER_QDBM = 80
ACK_TIMEOUT_SEC = 3.0


class FirmwareControlError(ValueError):
    """Raised when a firmware command cannot be validated or sent."""


class FirmwareController:
    """Create UART commands and consume the RX firmware's control responses."""

    def __init__(
        self,
        send_line: Callable[[str], None],
        *,
        status_callback: Callable[..., None] | None = None,
        request_clock: Callable[[], float] = time.time,
        ack_timeout_sec: float = ACK_TIMEOUT_SEC,
    ) -> None:
        self.send_line = send_line
        self.status_callback = status_callback
        self.request_clock = request_clock
        self.ack_timeout_sec = ack_timeout_sec
        self._lock = threading.Lock()
        self._last_request_id = 0
        self._pending: dict[int, threading.Timer] = {}
        self._limits = {
            "min_rate_hz": DEFAULT_MIN_RATE_HZ,
            "max_rate_hz": DEFAULT_MAX_RATE_HZ,
            "min_tx_power_qdbm": DEFAULT_MIN_TX_POWER_QDBM,
            "max_tx_power_qdbm": DEFAULT_MAX_TX_POWER_QDBM,
        }

    def set_settings(self, rate_hz: Any, tx_power_dbm: Any) -> dict[str, Any]:
        rate = self._integer(rate_hz, "rate_hz")
        power_dbm = self._finite_float(tx_power_dbm, "tx_power_dbm")
        power_qdbm = round(power_dbm * 4)
        if not math.isclose(power_qdbm / 4, power_dbm, abs_tol=1e-9):
            raise FirmwareControlError(
                "tx_power_dbm must use 0.25 dBm increments"
            )
        self._validate_limits(rate, power_qdbm)
        return self._send("SET", rate, power_qdbm)

    def request_settings(self) -> dict[str, Any]:
        return self._send("GET", None, None)

    def reset_settings(self) -> dict[str, Any]:
        return self._send("RESET", None, None)

    def handle_uart_line(self, line: str) -> bool:
        """Consume one control line, returning True when it is control traffic."""

        if not line.startswith(f"{CSI_CTRL_PREFIX}_"):
            return False
        try:
            fields = line.strip().split(",")
            if fields[0] == "CSI_CTRL_READY" and len(fields) == 7:
                self._handle_ready(fields)
            elif fields[0] in {"CSI_CTRL_SENT", "CSI_CTRL_ACK"} and len(fields) == 5:
                self._handle_status(fields)
            else:
                raise FirmwareControlError("unknown or malformed control line")
        except (FirmwareControlError, ValueError) as exc:
            self._status(
                "firmware_protocol_error",
                error=str(exc),
                raw_line=line[:256],
            )
        return True

    def _handle_ready(self, fields: list[str]) -> None:
        version, min_rate, max_rate, min_power, max_power = (
            int(value) for value in fields[1:6]
        )
        if version <= 0 or min_rate <= 0 or min_rate > max_rate:
            raise FirmwareControlError("invalid firmware limits")
        if min_power > max_power:
            raise FirmwareControlError("invalid firmware power limits")
        with self._lock:
            self._limits = {
                "min_rate_hz": min_rate,
                "max_rate_hz": max_rate,
                "min_tx_power_qdbm": min_power,
                "max_tx_power_qdbm": max_power,
            }
        self._status(
            "firmware_ready",
            protocol_version=version,
            min_rate_hz=min_rate,
            max_rate_hz=max_rate,
            min_tx_power_qdbm=min_power,
            max_tx_power_qdbm=max_power,
            receiver_mac=fields[6],
        )

    def _handle_status(self, fields: list[str]) -> None:
        request_id = int(fields[1])
        status_code = int(fields[2])
        rate_hz = int(fields[3])
        tx_power_qdbm = int(fields[4])
        event = (
            "firmware_command_sent"
            if fields[0] == "CSI_CTRL_SENT"
            else "firmware_command_ack"
        )
        if event == "firmware_command_ack":
            with self._lock:
                timer = self._pending.pop(request_id, None)
            if timer is not None:
                timer.cancel()
        self._status(
            event,
            request_id=request_id,
            status_code=status_code,
            rate_hz=rate_hz,
            tx_power_qdbm=tx_power_qdbm,
            error=(None if status_code == 0 else f"ESP error {status_code}"),
        )

    def _send(
        self,
        command: str,
        rate_hz: int | None,
        tx_power_qdbm: int | None,
    ) -> dict[str, Any]:
        with self._lock:
            if self._pending:
                raise FirmwareControlError(
                    "another firmware command is waiting for TX ACK"
                )
        request_id = self._next_request_id()
        fields: list[str | int] = [CSI_CTRL_PREFIX, command, request_id]
        if command == "SET":
            fields.extend([rate_hz or 0, tx_power_qdbm or 0])
        line = ",".join(str(field) for field in fields)
        self._status(
            "firmware_command_requested",
            request_id=request_id,
            command=command,
            rate_hz=rate_hz,
            tx_power_qdbm=tx_power_qdbm,
        )
        timer = threading.Timer(
            self.ack_timeout_sec,
            self._ack_timeout,
            args=(request_id, command),
        )
        timer.daemon = True
        with self._lock:
            self._pending[request_id] = timer
        timer.start()
        try:
            self.send_line(line)
        except Exception as exc:
            with self._lock:
                self._pending.pop(request_id, None)
            timer.cancel()
            self._status(
                "firmware_command_failed",
                request_id=request_id,
                command=command,
                error=str(exc),
            )
            raise FirmwareControlError(str(exc)) from exc
        return {
            "accepted": True,
            "request_id": request_id,
            "command": command,
            "rate_hz": rate_hz,
            "tx_power_qdbm": tx_power_qdbm,
            "tx_power_dbm": (
                None if tx_power_qdbm is None else tx_power_qdbm / 4
            ),
        }

    def _ack_timeout(self, request_id: int, command: str) -> None:
        with self._lock:
            if self._pending.pop(request_id, None) is None:
                return
        self._status(
            "firmware_command_failed",
            request_id=request_id,
            command=command,
            error="TX ACK timeout",
        )

    def _next_request_id(self) -> int:
        with self._lock:
            candidate = int(self.request_clock() * 1000) & 0xFFFFFFFF
            if candidate == 0 or candidate == self._last_request_id:
                candidate = (self._last_request_id + 1) & 0xFFFFFFFF
                if candidate == 0:
                    candidate = 1
            self._last_request_id = candidate
            return candidate

    def _validate_limits(self, rate_hz: int, tx_power_qdbm: int) -> None:
        with self._lock:
            limits = dict(self._limits)
        if not limits["min_rate_hz"] <= rate_hz <= limits["max_rate_hz"]:
            raise FirmwareControlError(
                f"rate_hz must be between {limits['min_rate_hz']} and "
                f"{limits['max_rate_hz']}"
            )
        if not (
            limits["min_tx_power_qdbm"]
            <= tx_power_qdbm
            <= limits["max_tx_power_qdbm"]
        ):
            raise FirmwareControlError(
                "tx_power_dbm must be between "
                f"{limits['min_tx_power_qdbm'] / 4:g} and "
                f"{limits['max_tx_power_qdbm'] / 4:g}"
            )

    @staticmethod
    def _integer(value: Any, name: str) -> int:
        if isinstance(value, bool):
            raise FirmwareControlError(f"{name} must be an integer")
        try:
            converted = int(value)
        except (TypeError, ValueError) as exc:
            raise FirmwareControlError(f"{name} must be an integer") from exc
        if isinstance(value, float) and not value.is_integer():
            raise FirmwareControlError(f"{name} must be an integer")
        if isinstance(value, str) and str(converted) != value.strip():
            raise FirmwareControlError(f"{name} must be an integer")
        return converted

    @staticmethod
    def _finite_float(value: Any, name: str) -> float:
        if isinstance(value, bool):
            raise FirmwareControlError(f"{name} must be a number")
        try:
            converted = float(value)
        except (TypeError, ValueError) as exc:
            raise FirmwareControlError(f"{name} must be a number") from exc
        if not math.isfinite(converted):
            raise FirmwareControlError(f"{name} must be finite")
        return converted

    def _status(self, event: str, **values: Any) -> None:
        if self.status_callback is not None:
            self.status_callback(event, **values)
