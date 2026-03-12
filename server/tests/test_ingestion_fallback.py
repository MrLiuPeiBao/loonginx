from __future__ import annotations

import json
import contextlib

from app.mqtt.client import MQTTMessageContext
from app.services import ingestion


def _broken_session_scope():
    @contextlib.contextmanager
    def _ctx():
        raise RuntimeError("db down")
        yield  # pragma: no cover
    return _ctx()


def test_sensor_ingestion_emits_alarm_on_db_error(monkeypatch) -> None:
    events = []

    def fake_publish(mqtt_manager, event, **kwargs):
        events.append(event)
        return True

    monkeypatch.setattr(ingestion, "session_scope", _broken_session_scope)
    monkeypatch.setattr(ingestion, "publish_alarm_event", fake_publish)

    payload = {
        "timestamp": "2024-01-01T00:00:00Z",
        "device_id": "dev",
        "location": "loc",
        "temperature": 20,
        "humidity": 50,
        "pressure": 101,
        "smoke": 0,
        "co": 0,
        "o2": 20,
        "h2s": 0,
        "ch4": 0,
    }
    context = MQTTMessageContext(
        topic="sensors/data",
        payload=json.dumps(payload).encode("utf-8"),
        qos=0,
        retain=False,
    )

    ingestion.handle_sensor_payload(context, mqtt_manager=object())

    assert events
    assert events[0].get("source") == "ingestion_failed"


def test_bms_ingestion_emits_alarm_on_db_error(monkeypatch) -> None:
    events = []

    def fake_publish(mqtt_manager, event, **kwargs):
        events.append(event)
        return True

    monkeypatch.setattr(ingestion, "session_scope", _broken_session_scope)
    monkeypatch.setattr(ingestion, "publish_alarm_event", fake_publish)

    payload = {
        "timestamp": "2024-01-01T00:00:00Z",
        "device_id": "dev",
        "location": "loc",
        "voltage": 12.5,
    }
    context = MQTTMessageContext(
        topic="sensors/bms",
        payload=json.dumps(payload).encode("utf-8"),
        qos=0,
        retain=False,
    )

    ingestion.handle_bms_payload(context, mqtt_manager=object())

    assert events
    assert events[0].get("source") == "ingestion_failed"
