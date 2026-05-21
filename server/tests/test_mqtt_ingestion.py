from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.db.models import RFIDData, SensorData
from app.mqtt import MQTTMessageContext
from app.runtime.db_worker import DBWorker
from app.services.data_service import SENSOR_VALUE_FIELDS
from app.services.ingestion import handle_rfid_payload, handle_sensor_payload


def _build_context(payload: object) -> MQTTMessageContext:
    return MQTTMessageContext(
        topic="sensors/data",
        payload=json.dumps(payload).encode("utf-8"),
        qos=0,
        retain=False,
    )


def test_handle_sensor_payload_persists_partial_sensor_batch(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr("app.runtime.db_worker.engine", engine)

    worker = DBWorker(name="test-db-worker")
    worker.start()
    try:
        payload = [
            {
                "sensor_type": "temperature",
                "value": 26.5,
                "timestamp": "2026-05-11T18:20:00+08:00",
                "device_id": "gateway-01",
                "location": "shaft-a",
            },
            {
                "sensor_type": "co",
                "value": 4.2,
                "timestamp": "2026-05-11T18:20:00+08:00",
                "device_id": "gateway-01",
                "location": "shaft-a",
            },
        ]

        handle_sensor_payload(_build_context(payload), db_worker=worker)

        with Session(engine) as session:
            rows = session.exec(select(SensorData)).all()
        assert len(rows) == 1
        row = rows[0]
        assert row.device_id == "sensors/data"
        assert row.location == "shaft-a"
        assert row.temperature == 26.5
        assert row.co == 4.2
        missing_fields = [field for field in SENSOR_VALUE_FIELDS if field not in {"temperature", "co"}]
        assert all(getattr(row, field) is None for field in missing_fields)
    finally:
        worker.stop()


def test_handle_sensor_payload_discards_empty_sensor_values(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr("app.runtime.db_worker.engine", engine)

    worker = DBWorker(name="test-db-worker")
    worker.start()
    try:
        payload = {
            "timestamp": datetime.now().isoformat(),
            "device_id": "gateway-01",
            "location": "shaft-a",
            "readings": [
                {"type": "temperature", "value": None},
                {"type": "co", "value": "not-a-number"},
            ],
        }

        handle_sensor_payload(_build_context(payload), db_worker=worker)

        with Session(engine) as session:
            rows = session.exec(select(SensorData)).all()
        assert rows == []
    finally:
        worker.stop()


def test_handle_sensor_payload_backfills_missing_values_from_previous_row(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr("app.runtime.db_worker.engine", engine)

    worker = DBWorker(name="test-db-worker")
    worker.start()
    try:
        first_payload = [
            {
                "sensor_type": "temperature",
                "value": 26.5,
                "timestamp": "2026-05-13T10:38:00+08:00",
                "device_id": "gateway-01",
                "location": "shaft-a",
            },
            {
                "sensor_type": "co",
                "value": 4.2,
                "timestamp": "2026-05-13T10:38:00+08:00",
                "device_id": "gateway-01",
                "location": "shaft-a",
            },
            {
                "sensor_type": "o2",
                "value": 20.9,
                "timestamp": "2026-05-13T10:38:00+08:00",
                "device_id": "gateway-01",
                "location": "shaft-a",
            },
        ]
        second_payload = [
            {
                "sensor_type": "o2",
                "value": 21.1,
                "timestamp": "2026-05-13T10:38:03+08:00",
                "device_id": "gateway-01",
                "location": "shaft-a",
            }
        ]

        handle_sensor_payload(_build_context(first_payload), db_worker=worker)
        handle_sensor_payload(_build_context(second_payload), db_worker=worker)

        with Session(engine) as session:
            rows = session.exec(select(SensorData).order_by(SensorData.timestamp.asc(), SensorData.id.asc())).all()
        assert len(rows) == 2
        latest = rows[-1]
        assert latest.device_id == "sensors/data"
        assert latest.location == "shaft-a"
        assert latest.temperature == 26.5
        assert latest.co == 4.2
        assert latest.o2 == 21.1
    finally:
        worker.stop()


def test_handle_sensor_payload_does_not_backfill_stale_previous_row(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr("app.runtime.db_worker.engine", engine)
    monkeypatch.setattr("app.services.ingestion.get_settings", lambda: type("S", (), {"prefer_payload_device_id": False, "sensor_backfill_max_age_seconds": 5.0})())

    worker = DBWorker(name="test-db-worker")
    worker.start()
    try:
        first_payload = [
            {
                "sensor_type": "temperature",
                "value": 26.5,
                "timestamp": "2026-05-13T10:38:00+08:00",
                "device_id": "gateway-01",
                "location": "shaft-a",
            },
            {
                "sensor_type": "co",
                "value": 4.2,
                "timestamp": "2026-05-13T10:38:00+08:00",
                "device_id": "gateway-01",
                "location": "shaft-a",
            },
        ]
        second_payload = [
            {
                "sensor_type": "o2",
                "value": 21.1,
                "timestamp": "2026-05-13T10:40:00+08:00",
                "device_id": "gateway-01",
                "location": "shaft-a",
            }
        ]

        handle_sensor_payload(_build_context(first_payload), db_worker=worker)
        handle_sensor_payload(_build_context(second_payload), db_worker=worker)

        with Session(engine) as session:
            rows = session.exec(select(SensorData).order_by(SensorData.timestamp.asc(), SensorData.id.asc())).all()
        assert len(rows) == 2
        latest = rows[-1]
        assert latest.temperature is None
        assert latest.co is None
        assert latest.o2 == 21.1
    finally:
        worker.stop()


def test_handle_rfid_payload_persists_single_record(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr("app.runtime.db_worker.engine", engine)

    worker = DBWorker(name="test-db-worker")
    worker.start()
    try:
        payload = {
            "card_id": "11 22 33 44",
            "raw_data": "11223344",
            "timestamp": "2026-05-14T10:00:00+08:00",
        }
        context = MQTTMessageContext(
            topic="sensors/rfid",
            payload=json.dumps(payload).encode("utf-8"),
            qos=0,
            retain=False,
        )

        handle_rfid_payload(context, db_worker=worker)

        with Session(engine) as session:
            rows = session.exec(select(RFIDData)).all()
        assert len(rows) == 1
        assert rows[0].device_id == "sensors/rfid"
        assert rows[0].card_id == "11 22 33 44"
        assert rows[0].raw_data == "11223344"
    finally:
        worker.stop()


def test_handle_rfid_payload_accepts_list_payload(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr("app.runtime.db_worker.engine", engine)

    worker = DBWorker(name="test-db-worker")
    worker.start()
    try:
        payload = [
            {
                "card_id": "aa bb",
                "raw_data": "aabb",
                "timestamp": "2026-05-14T10:00:00+08:00",
            },
            {
                "card_id": "cc dd",
                "raw_data": "ccdd",
                "timestamp": "2026-05-14T10:00:01+08:00",
            },
        ]
        context = MQTTMessageContext(
            topic="sensors/rfid",
            payload=json.dumps(payload).encode("utf-8"),
            qos=0,
            retain=False,
        )

        handle_rfid_payload(context, db_worker=worker)

        with Session(engine) as session:
            rows = session.exec(select(RFIDData).order_by(RFIDData.timestamp.asc())).all()
        assert len(rows) == 2
        assert rows[0].card_id == "aa bb"
        assert rows[1].card_id == "cc dd"
    finally:
        worker.stop()
