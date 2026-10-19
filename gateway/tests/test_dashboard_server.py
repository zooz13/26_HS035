import json
from pathlib import Path
from unittest.mock import Mock
from urllib.request import Request, urlopen

from app.dashboard_server import DashboardHttpServer


def test_dashboard_serves_assets_and_accepts_settings(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("dashboard", encoding="utf-8")
    firmware = Mock()
    firmware.set_settings.return_value = {
        "accepted": True,
        "request_id": 7,
        "command": "SET",
        "rate_hz": 40,
        "tx_power_qdbm": 60,
        "tx_power_dbm": 15.0,
    }
    server = DashboardHttpServer(
        host="127.0.0.1",
        port=0,
        dashboard_dir=tmp_path,
        firmware=firmware,
        status_snapshot=lambda: {"schema_version": "1.0", "live": True},
    )
    server.start()
    assert server.address is not None
    base_url = f"http://{server.address[0]}:{server.address[1]}"
    try:
        with urlopen(f"{base_url}/", timeout=2) as response:
            assert response.read() == b"dashboard"
        with urlopen(f"{base_url}/status.json", timeout=2) as response:
            assert json.load(response) == {
                "schema_version": "1.0",
                "live": True,
            }
        request = Request(
            f"{base_url}/api/firmware/settings",
            data=json.dumps(
                {"rate_hz": 40, "tx_power_dbm": 15.0}
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=2) as response:
            assert response.status == 202
            assert json.load(response)["request_id"] == 7
    finally:
        server.stop()

    firmware.set_settings.assert_called_once_with(40, 15.0)
