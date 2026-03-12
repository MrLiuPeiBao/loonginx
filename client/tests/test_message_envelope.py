from __future__ import annotations

from client.utils.message_envelope import with_payload_type


def test_with_payload_type_sets_field() -> None:
    payload = {"a": 1}
    result = with_payload_type(payload, "sensor_data")
    assert result["payload_type"] == "sensor_data"
