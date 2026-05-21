from __future__ import annotations

import asyncio
import sys
import types


def _install_dummy_modules() -> None:
    minimalmodbus_mod = types.ModuleType("minimalmodbus")

    class DummyInstrument:
        def __init__(self, *args, **kwargs):
            self.serial = types.SimpleNamespace(is_open=False)

    minimalmodbus_mod.Instrument = DummyInstrument
    sys.modules["minimalmodbus"] = minimalmodbus_mod

    serial_mod = types.ModuleType("serial")
    serial_mod.PARITY_NONE = "N"

    class DummySerial:
        pass

    serial_mod.Serial = DummySerial
    sys.modules["serial"] = serial_mod

    mqtt_mod = types.ModuleType("paho.mqtt.client")
    mqtt_mod.MQTT_ERR_SUCCESS = 0

    class DummyMQTTClient:
        def __init__(self, *args, **kwargs):
            pass

        def username_pw_set(self, *args, **kwargs):
            pass

    mqtt_mod.Client = DummyMQTTClient
    sys.modules["paho"] = types.ModuleType("paho")
    sys.modules["paho.mqtt"] = types.ModuleType("paho.mqtt")
    sys.modules["paho.mqtt.client"] = mqtt_mod


_install_dummy_modules()

from main import SensorGateway  # noqa: E402


def test_gateway_applies_threshold_alignment_hot_update(monkeypatch) -> None:
    persisted = {}
    monkeypatch.setattr("main.load_overrides", lambda: {})
    monkeypatch.setattr("main.save_overrides", lambda overrides: persisted.update(overrides))

    gateway = SensorGateway()

    applied = gateway._apply_hot_overrides(
        {
            "LOCAL_SENSOR_THRESHOLDS": {
                "co": {"min": 0, "max": 5},
                "o2": {"min": "19.5", "max": "23.5"},
            }
        }
    )

    assert "LOCAL_SENSOR_THRESHOLDS" in applied
    assert gateway.local_sensor_thresholds["co"] == {"min": 0.0, "max": 5.0}
    assert gateway.local_sensor_thresholds["o2"] == {"min": 19.5, "max": 23.5}
    assert persisted["LOCAL_SENSOR_THRESHOLDS"]["co"] == {"min": 0.0, "max": 5.0}
    assert gateway._config_aligned is True


def test_device_hello_includes_retry_metadata() -> None:
    gateway = SensorGateway()
    published = []

    class DummyMQTT:
        def publish(self, topic, payload):
            published.append((topic, payload))
            return True

    gateway.mqtt_client = DummyMQTT()
    gateway.send_device_hello(reason="retry")

    assert published[0][0] == "device/hello"
    assert published[0][1]["reason"] == "retry"
    assert published[0][1]["attempt"] == 1


def test_local_alarm_only_uses_dangerous_gas_sensors() -> None:
    gateway = SensorGateway()
    gateway.local_sensor_thresholds = {
        "temperature": {"min": 10.0, "max": 35.0},
        "co": {"min": 0.0, "max": 35.0},
    }

    assert gateway._find_threshold_violations(
        [{"sensor_type": "temperature", "value": 50.0}]
    ) == []

    violations = gateway._find_threshold_violations(
        [{"sensor_type": "co", "value": 50.0}]
    )
    assert violations[0]["sensor_type"] == "co"


def test_alarm_broadcast_only_accepts_dangerous_gas_events() -> None:
    gateway = SensorGateway()

    assert not gateway._is_dangerous_gas_alarm_event(
        {"event": "alarm", "source": "yolo_person", "payload": {"sensor_key": "person"}}
    )
    assert not gateway._is_dangerous_gas_alarm_event(
        {"event": "alarm", "source": "sensor_threshold", "payload": {"sensor_key": "temperature"}}
    )
    assert gateway._is_dangerous_gas_alarm_event(
        {"event": "alarm", "source": "sensor_threshold", "payload": {"sensor_key": "h2s"}}
    )


def test_gateway_only_initializes_enabled_sensors() -> None:
    gateway = SensorGateway()
    gateway.enabled_sensor_types = {"co", "o2", "photoelectric"}

    gateway.initialize_sensors()

    assert set(gateway.sensors.keys()) == {"co", "o2", "photoelectric"}


def test_sensor_specific_max_attempts_overrides_gateway_default() -> None:
    gateway = SensorGateway()
    gateway.max_sensor_attempts = 3

    class DummySensor:
        name = "temperature"
        address = 15
        max_attempts = 1

        def get_formatted_data(self):
            return None

    result = asyncio.run(gateway._read_sensor_with_retries(DummySensor()))
    assert result is None


def test_sensor_retry_recovers_second_attempt() -> None:
    gateway = SensorGateway()
    gateway.sensor_retry_delay = 0.0

    class DummySensor:
        name = "photoelectric"
        address = 25
        max_attempts = 2
        response_timeout = 0.25

        def __init__(self):
            self.calls = 0

        def get_formatted_data(self):
            self.calls += 1
            if self.calls == 1:
                return None
            return {"sensor_type": self.name, "address": self.address, "value": 1}

    sensor = DummySensor()
    result = asyncio.run(gateway._read_sensor_with_retries(sensor))

    assert result == {"sensor_type": "photoelectric", "address": 25, "value": 1}
    assert sensor.calls == 2
    assert sensor._last_read_attempt_count == 2
    assert sensor._last_first_attempt_success is False
    assert sensor._last_read_recovered is True


def test_transaction_metrics_are_recorded() -> None:
    gateway = SensorGateway()

    gateway._record_transaction_metric(
        operation="direct_read",
        address=15,
        status="timeout",
        duration_ms=123.4,
    )

    counts = gateway._transaction_metrics["counts"]["direct_read:15"]
    last = gateway._transaction_metrics["last"]["direct_read:15"]

    assert counts["timeout"] == 1
    assert last["duration_ms"] == 123.4


def test_stale_payload_reuses_recent_value() -> None:
    gateway = SensorGateway()

    class DummySensor:
        name = "temperature"
        address = 15
        last_value = 27.5
        _last_success_ts = __import__("time").time()

    payload = gateway._build_stale_sensor_payload(DummySensor())

    assert payload is not None
    assert payload["value"] == 27.5
    assert payload["stale"] is True


def test_stale_payload_prefers_last_full_payload() -> None:
    gateway = SensorGateway()

    class DummySensor:
        name = "photoelectric"
        address = 25
        last_value = {"value": 0}
        _last_success_ts = __import__("time").time()
        _last_payload = {
            "sensor_type": "photoelectric",
            "address": 25,
            "value": 0,
            "obstacle_detected": False,
            "raw_mask": 0,
            "raw_hex": "19 02 01 00 a7 28",
            "timestamp": "2026-05-14T10:00:00+08:00",
            "ts": __import__("time").time(),
        }

    payload = gateway._build_stale_sensor_payload(DummySensor())

    assert payload is not None
    assert payload["sensor_type"] == "photoelectric"
    assert payload["raw_hex"] == "19 02 01 00 a7 28"
    assert payload["stale"] is True
