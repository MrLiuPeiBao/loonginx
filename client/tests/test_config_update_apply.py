"""Config update apply tests."""

from __future__ import annotations


def test_apply_config_update_marks_pending_restart() -> None:
    from utils.config_override import split_hot_and_restart

    hot, pending = split_hot_and_restart({"MQTT_BROKER": "x"})
    assert "MQTT_BROKER" in pending
