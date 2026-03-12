"""Config sync payload tests."""

from __future__ import annotations

from app.services.config_sync import build_config_update_payload


def test_build_config_update_payload() -> None:
    payload = build_config_update_payload({"SENSOR_POLL_DELAY": 0.1}, version=2)
    assert payload["version"] == 2
    assert "SENSOR_POLL_DELAY" in payload["payload"]
