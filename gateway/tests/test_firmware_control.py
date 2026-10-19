import time

import pytest

from app.firmware_control import FirmwareControlError, FirmwareController


def make_controller(**overrides: object):
    lines: list[str] = []
    events: list[tuple[str, dict[str, object]]] = []

    def status(event: str, **values: object) -> None:
        events.append((event, values))

    values = {
        "send_line": lines.append,
        "status_callback": status,
        "request_clock": lambda: 1234.567,
        "ack_timeout_sec": 1.0,
    }
    values.update(overrides)
    controller = FirmwareController(**values)  # type: ignore[arg-type]
    return controller, lines, events


def test_set_converts_dbm_to_quarter_dbm_and_consumes_ack() -> None:
    controller, lines, events = make_controller()

    result = controller.set_settings(40, 15.0)
    request_id = result["request_id"]
    assert lines == [f"CSI_CTRL,SET,{request_id},40,60"]
    assert result["tx_power_qdbm"] == 60

    assert controller.handle_uart_line(
        f"CSI_CTRL_ACK,{request_id},0,40,60"
    ) is True
    assert events[-1] == (
        "firmware_command_ack",
        {
            "request_id": request_id,
            "status_code": 0,
            "rate_hz": 40,
            "tx_power_qdbm": 60,
            "error": None,
        },
    )


def test_ready_updates_dynamic_limits() -> None:
    controller, _, events = make_controller()

    assert controller.handle_uart_line(
        "CSI_CTRL_READY,1,10,80,20,72,aa:bb:cc:dd:ee:ff"
    ) is True
    assert events[-1][0] == "firmware_ready"

    with pytest.raises(FirmwareControlError, match="between 10 and 80"):
        controller.set_settings(9, 15)
    with pytest.raises(FirmwareControlError, match="between 5 and 18"):
        controller.set_settings(40, 20)


@pytest.mark.parametrize("power", [15.1, float("nan"), float("inf")])
def test_set_rejects_invalid_power(power: float) -> None:
    controller, _, _ = make_controller()

    with pytest.raises(FirmwareControlError):
        controller.set_settings(40, power)


def test_non_control_line_is_not_consumed() -> None:
    controller, _, events = make_controller()

    assert controller.handle_uart_line("CSI_DATA,1") is False
    assert events == []


def test_ack_timeout_is_reported() -> None:
    controller, _, events = make_controller(ack_timeout_sec=0.01)
    result = controller.request_settings()

    time.sleep(0.05)

    assert (
        "firmware_command_failed",
        {
            "request_id": result["request_id"],
            "command": "GET",
            "error": "TX ACK timeout",
        },
    ) in events


def test_only_one_command_can_wait_for_ack() -> None:
    controller, _, _ = make_controller()
    controller.request_settings()

    with pytest.raises(FirmwareControlError, match="another firmware command"):
        controller.reset_settings()
