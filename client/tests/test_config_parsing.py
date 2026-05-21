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


def test_local_sensor_thresholds_json(monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_SENSOR_THRESHOLDS", '{"co":{"min":0,"max":10},"o2":{"min":19.5,"max":23.5}}')
    config = importlib.import_module("client.config")
    importlib.reload(config)
    assert config.LOCAL_SENSOR_THRESHOLDS["co"] == {"min": 0.0, "max": 10.0}
    assert config.LOCAL_SENSOR_THRESHOLDS["o2"] == {"min": 19.5, "max": 23.5}


def test_local_sensor_thresholds_runtime_override(monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_SENSOR_THRESHOLDS", '{"co":{"min":0,"max":35}}')
    overrides = {
        "LOCAL_SENSOR_THRESHOLDS": {
            "co": {"min": 1, "max": 6},
            "o2": {"min": "20", "max": "22"},
        },
    }
    config = importlib.import_module("client.config")
    monkeypatch.setattr("utils.config_override.load_overrides", lambda: overrides)
    importlib.reload(config)
    assert config.LOCAL_SENSOR_THRESHOLDS["co"] == {"min": 1.0, "max": 6.0}
    assert config.LOCAL_SENSOR_THRESHOLDS["o2"] == {"min": 20.0, "max": 22.0}


def test_camera_host_from_rtsp_target(monkeypatch) -> None:
    monkeypatch.setenv("CAMERA_TARGET", "rtsp://admin:pwd@192.168.0.101:554/Streaming/Channels/101")
    config = importlib.import_module("client.config")
    importlib.reload(config)
    assert config.CAMERA_HOST == "192.168.0.101"


def test_enabled_sensor_types_parsing(monkeypatch) -> None:
    monkeypatch.setenv("ENABLED_SENSOR_TYPES", "co,h2s,o2,ch4,photoelectric,bms")
    config = importlib.import_module("client.config")
    importlib.reload(config)
    assert config.ENABLED_SENSOR_TYPES == ["co", "h2s", "o2", "ch4", "photoelectric", "bms"]
