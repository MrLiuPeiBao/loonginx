"""Device hello payload tests."""

from __future__ import annotations

from app.services.config_sync import build_device_hello


def test_device_hello_payload_contains_version() -> None:
    payload = build_device_hello(version=3)
    assert payload["config_version"] == 3
