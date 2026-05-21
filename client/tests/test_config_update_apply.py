"""Config update apply tests."""

from __future__ import annotations


def test_apply_config_update_marks_pending_restart() -> None:
    from utils.config_override import split_hot_and_restart

    hot, pending = split_hot_and_restart({"MQTT_BROKER": "x"})
    assert "MQTT_BROKER" in pending


def test_local_sensor_thresholds_are_hot_update() -> None:
    from utils.config_override import split_hot_and_restart

    hot, pending = split_hot_and_restart({"LOCAL_SENSOR_THRESHOLDS": {"co": {"min": 0, "max": 5}}})

    assert hot == {"LOCAL_SENSOR_THRESHOLDS": {"co": {"min": 0, "max": 5}}}
    assert pending == {}


def test_enabled_sensor_types_are_hot_update() -> None:
    from utils.config_override import split_hot_and_restart

    hot, pending = split_hot_and_restart({"ENABLED_SENSOR_TYPES": ["co", "h2s", "o2", "ch4", "photoelectric", "bms"]})

    assert hot == {"ENABLED_SENSOR_TYPES": ["co", "h2s", "o2", "ch4", "photoelectric", "bms"]}
    assert pending == {}
