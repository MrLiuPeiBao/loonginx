"""Query limit defaults tests."""

from __future__ import annotations

import base64
import os
from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db.models import AlarmRecord, AlarmType, AudioData, ImageData, MetalAnomaly, SensorData


def _build_client(session) -> TestClient:
    os.environ["APP_TEST_MODE"] = "1"
    from app.api.routes import router
    from app.db.session import get_session

    app = FastAPI()

    def _override_get_session():
        yield session

    app.dependency_overrides[get_session] = _override_get_session
    app.include_router(router)
    return TestClient(app)


def test_list_sensor_data_default_limit(session) -> None:
    now = datetime.now()
    old = now - timedelta(days=2)
    session.add(
        SensorData(
            timestamp=old,
            device_id="dev-old",
            location="lab",
        )
    )
    session.add(
        SensorData(
            timestamp=now,
            device_id="dev-new",
            location="lab",
        )
    )
    session.commit()

    client = _build_client(session)
    resp = client.get("/api/sensors")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["total"] == 1
    assert any(item["device_id"] == "dev-new" for item in payload["items"])
    assert all(item["device_id"] != "dev-old" for item in payload["items"])


def test_history_endpoints_include_total(session) -> None:
    now = datetime.now()
    session.add(SensorData(timestamp=now - timedelta(minutes=5), device_id="sensor-1", location="lab"))
    session.add(SensorData(timestamp=now, device_id="sensor-2", location="lab"))
    session.add(
        AlarmRecord(
            timestamp=now - timedelta(minutes=2),
            sensor_key="temperature",
            sensor_name="Temperature",
            value=81.0,
            unit="C",
            min_threshold=0.0,
            max_threshold=80.0,
            alarm_type=AlarmType.HIGH,
            is_handled=False,
            location="lab",
        )
    )
    session.add(
        AlarmRecord(
            timestamp=now,
            sensor_key="temperature",
            sensor_name="Temperature",
            value=82.0,
            unit="C",
            min_threshold=0.0,
            max_threshold=80.0,
            alarm_type=AlarmType.HIGH,
            is_handled=True,
            location="lab",
        )
    )
    session.add(
        ImageData(
            timestamp=now - timedelta(minutes=1),
            device_id="cam-1",
            image_name="a.jpg",
            image_data=base64.b64encode(b"img-a").decode(),
            location="lab",
        )
    )
    session.add(
        ImageData(
            timestamp=now,
            device_id="cam-1",
            image_name="b.jpg",
            image_data=base64.b64encode(b"img-b").decode(),
            location="lab",
        )
    )
    session.add(
        AudioData(
            timestamp=now - timedelta(minutes=1),
            device_id="mic-1",
            audio_name="a.wav",
            audio_data=b"audio-a",
            location="lab",
        )
    )
    session.add(
        AudioData(
            timestamp=now,
            device_id="mic-1",
            audio_name="b.wav",
            audio_data=b"audio-b",
            location="lab",
        )
    )
    session.add(MetalAnomaly(timestamp=now - timedelta(minutes=1), device_id="metal-1", location="lab"))
    session.add(MetalAnomaly(timestamp=now, device_id="metal-1", location="lab"))
    session.commit()

    client = _build_client(session)

    sensor_resp = client.get("/api/sensors", params={"limit": 1})
    assert sensor_resp.status_code == 200
    sensor_payload = sensor_resp.json()
    assert sensor_payload["total"] == 2
    assert len(sensor_payload["items"]) == 1

    alarm_resp = client.get("/api/alarms", params={"limit": 1})
    assert alarm_resp.status_code == 200
    alarm_payload = alarm_resp.json()
    assert alarm_payload["total"] == 2
    assert len(alarm_payload["items"]) == 1

    image_resp = client.get("/api/images", params={"limit": 1})
    assert image_resp.status_code == 200
    image_payload = image_resp.json()
    assert image_payload["total"] == 2
    assert len(image_payload["items"]) == 1

    audio_resp = client.get("/api/audio", params={"limit": 1})
    assert audio_resp.status_code == 200
    audio_payload = audio_resp.json()
    assert audio_payload["total"] == 2
    assert len(audio_payload["items"]) == 1

    metal_resp = client.get("/api/metal-anomaly", params={"limit": 1})
    assert metal_resp.status_code == 200
    metal_payload = metal_resp.json()
    assert metal_payload["total"] == 2
    assert len(metal_payload["items"]) == 1
