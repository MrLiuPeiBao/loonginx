"""Query limit defaults tests."""

from __future__ import annotations

import base64
import os
from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db.models import (
    AlarmRecord,
    AlarmType,
    AudioData,
    BMSData,
    CablewayStatus,
    CommandDirection,
    CommandLog,
    CommandRequestState,
    CommandStatus,
    ImageData,
    MetalAnomaly,
    RFIDData,
    SensorData,
)


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
    session.add(BMSData(timestamp=now - timedelta(minutes=5), device_id="bms-1", location="lab"))
    session.add(BMSData(timestamp=now, device_id="bms-2", location="lab"))
    session.add(
        RFIDData(
            timestamp=now - timedelta(minutes=5),
            device_id="rfid-1",
            card_id="card-a",
            raw_data="AA BB",
            length=2,
            location="lab",
        )
    )
    session.add(
        RFIDData(
            timestamp=now,
            device_id="rfid-2",
            card_id="card-b",
            raw_data="CC DD",
            length=2,
            location="lab",
        )
    )
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
    session.add(
        CablewayStatus(
            timestamp=now - timedelta(minutes=1),
            device_id="plc-1",
            location="lab",
            plc_host="127.0.0.1",
            status={"running": False},
        )
    )
    session.add(
        CablewayStatus(
            timestamp=now,
            device_id="plc-1",
            location="lab",
            plc_host="127.0.0.1",
            status={"running": True},
        )
    )
    session.add(
        CommandLog(
            timestamp=now - timedelta(minutes=1),
            direction=CommandDirection.REQUEST,
            payload="01 02 03",
            notes="req",
            device_id="gw-1",
        )
    )
    session.add(
        CommandLog(
            timestamp=now,
            direction=CommandDirection.RESPONSE,
            payload="AA BB",
            notes="resp",
            device_id="gw-1",
        )
    )
    session.add(
        CommandRequestState(
            request_id="req-1",
            status=CommandStatus.SENT,
            command_type="generic",
            device_id="gw-1",
            request_payload="01 02 03",
            created_at=now - timedelta(minutes=1),
            updated_at=now - timedelta(minutes=1),
        )
    )
    session.add(
        CommandRequestState(
            request_id="req-2",
            status=CommandStatus.ACK,
            command_type="generic",
            device_id="gw-1",
            request_payload="01 02 03",
            response_payload="AA BB",
            created_at=now,
            updated_at=now,
        )
    )
    session.commit()

    client = _build_client(session)

    sensor_resp = client.get("/api/sensors", params={"limit": 1})
    assert sensor_resp.status_code == 200
    sensor_payload = sensor_resp.json()
    assert sensor_payload["total"] == 2
    assert len(sensor_payload["items"]) == 1

    bms_resp = client.get("/api/bms", params={"limit": 1})
    assert bms_resp.status_code == 200
    bms_payload = bms_resp.json()
    assert bms_payload["total"] == 2
    assert len(bms_payload["items"]) == 1

    rfid_resp = client.get("/api/rfid", params={"limit": 1})
    assert rfid_resp.status_code == 200
    rfid_payload = rfid_resp.json()
    assert rfid_payload["total"] == 2
    assert len(rfid_payload["items"]) == 1

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

    cableway_resp = client.get("/api/cableway/status", params={"limit": 1})
    assert cableway_resp.status_code == 200
    cableway_payload = cableway_resp.json()
    assert cableway_payload["total"] == 2
    assert len(cableway_payload["items"]) == 1

    commands_resp = client.get("/api/commands", params={"limit": 1})
    assert commands_resp.status_code == 200
    commands_payload = commands_resp.json()
    assert commands_payload["total"] == 2
    assert len(commands_payload["items"]) == 1

    requests_resp = client.get("/api/command-requests", params={"limit": 1})
    assert requests_resp.status_code == 200
    requests_payload = requests_resp.json()
    assert requests_payload["total"] == 2
    assert len(requests_payload["items"]) == 1
