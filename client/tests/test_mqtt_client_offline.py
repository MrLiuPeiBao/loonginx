from __future__ import annotations

import sys
import types


def _install_dummy_paho():
    mqtt_client = types.ModuleType("paho.mqtt.client")

    class DummyResult:
        def __init__(self, rc=0):
            self.rc = rc

    class DummyClient:
        def __init__(self, *args, **kwargs):
            self.on_connect = None
            self.on_message = None
            self.on_disconnect = None

        def connect(self, *args, **kwargs):
            return 0

        def loop_start(self):
            return None

        def publish(self, *args, **kwargs):
            return DummyResult(0)

        def subscribe(self, *args, **kwargs):
            return (0, 0)

        def reconnect(self):
            return 0

        def disconnect(self):
            return 0

    mqtt_client.Client = DummyClient
    mqtt_client.MQTT_ERR_SUCCESS = 0

    mqtt_pkg = types.ModuleType("paho")
    mqtt_sub = types.ModuleType("paho.mqtt")
    sys.modules["paho"] = mqtt_pkg
    sys.modules["paho.mqtt"] = mqtt_sub
    sys.modules["paho.mqtt.client"] = mqtt_client
    minimalmodbus_mod = types.ModuleType("minimalmodbus")
    class DummyInstrument:
        def __init__(self, *args, **kwargs):
            self.serial = types.SimpleNamespace()
    minimalmodbus_mod.Instrument = DummyInstrument
    sys.modules["minimalmodbus"] = minimalmodbus_mod
    serial_mod = types.ModuleType("serial")
    serial_mod.PARITY_NONE = "N"
    class DummySerial:
        pass
    serial_mod.Serial = DummySerial
    sys.modules["serial"] = serial_mod


def test_offline_latest_queue(monkeypatch) -> None:
    _install_dummy_paho()

    from client.communication.mqtt_client import MQTTClient

    config = {
        "broker": "localhost",
        "port": 1883,
        "client_id": "test",
        "publish_qos": 0,
        "offline_queue_enabled": True,
        "offline_queue_max_items": 10,
        "offline_queue_max_bytes": 10000,
        "offline_policy": "latest",
        "topic_qos_map": {"topic/a": 1},
    }
    client = MQTTClient(config)
    client.connected = False

    ok = client.publish("topic/a", {"v": 1})

    assert ok is True
    assert "topic/a" in getattr(client, "_offline_latest", {})
    assert client._resolve_qos("topic/a") == 1
