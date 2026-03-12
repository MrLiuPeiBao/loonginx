from __future__ import annotations

import importlib


def test_mqtt_topic_qos_map_json(monkeypatch) -> None:
    monkeypatch.setenv("MQTT_TOPIC_QOS_MAP", '{"sensors/data":0,"sensors/command/request":1}')
    config = importlib.import_module("client.config")
    importlib.reload(config)
    assert config.MQTT_TOPIC_QOS_MAP == {"sensors/data": 0, "sensors/command/request": 1}


def test_mqtt_topic_qos_map_kv(monkeypatch) -> None:
    monkeypatch.setenv("MQTT_TOPIC_QOS_MAP", "a=1,b=2")
    config = importlib.import_module("client.config")
    importlib.reload(config)
    assert config.MQTT_TOPIC_QOS_MAP == {"a": 1, "b": 2}


def test_mqtt_offline_policy_default(monkeypatch) -> None:
    monkeypatch.delenv("MQTT_OFFLINE_POLICY", raising=False)
    config = importlib.import_module("client.config")
    importlib.reload(config)
    assert config.MQTT_OFFLINE_POLICY == "queue"
