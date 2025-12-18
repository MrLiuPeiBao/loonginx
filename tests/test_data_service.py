"""DataService behavior tests."""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlmodel import select

from app.db.models import AlarmRecord, AlarmType, SensorConfig, SensorData
from app.services.data_service import DataService


def _insert_temperature_config(session, max_threshold: float = 22.0) -> None:
    """Insert a temperature threshold config to trigger alarms during tests."""
    config = SensorConfig(
        type="temperature",
        description="Temp",
        unit="C",
        min_threshold=10.0,
        max_threshold=max_threshold,
    )
    session.add(config)
    session.commit()


def test_create_sensor_data_merges_and_triggers_alarm(session) -> None:
    """Merging same-timestamp rows updates the record and emits one alarm."""
    _insert_temperature_config(session)
    service = DataService(session)
    timestamp = datetime(2024, 1, 1, 12, 0, 0)

    initial = SensorData(
        timestamp=timestamp,
        device_id="dev-1",
        location="lab",
        temperature=20.0,
        humidity=55.0,
    )
    updated = SensorData(
        timestamp=timestamp,
        device_id="dev-1",
        location="lab",
        temperature=25.0,
        humidity=55.0,
    )

    result = service.create_sensor_data([initial, updated])

    rows = session.exec(select(SensorData)).all()
    assert len(rows) == 1
    stored = rows[0]
    assert stored.temperature == pytest.approx(25.0)
    assert stored.humidity == pytest.approx(55.0)
    assert result and result[0].id == stored.id

    alarms = session.exec(select(AlarmRecord)).all()
    assert len(alarms) == 1
    alarm = alarms[0]
    assert alarm.sensor_key == "temperature"
    assert alarm.alarm_type == AlarmType.HIGH
    assert alarm.location == "lab"


def test_threshold_deduplication_by_timestamp(session) -> None:
    """Duplicate alarms are suppressed for the same timestamp/location."""
    _insert_temperature_config(session, max_threshold=24.0)
    service = DataService(session)
    timestamp = datetime(2024, 1, 1, 0, 0, 0)

    records = [
        SensorData(
            timestamp=timestamp,
            device_id="dev-2",
            location="zone-a",
            temperature=20.0,
        ),
        SensorData(
            timestamp=timestamp,
            device_id="dev-2",
            location="zone-a",
            temperature=26.0,
        ),
        SensorData(
            timestamp=timestamp,
            device_id="dev-2",
            location="zone-a",
            temperature=27.0,
        ),
    ]

    service.create_sensor_data(records)

    stored_sensor = session.exec(select(SensorData)).one()
    assert stored_sensor.temperature == pytest.approx(27.0)

    alarms = session.exec(select(AlarmRecord)).all()
    assert len(alarms) == 1
    alarm = alarms[0]
    assert alarm.value == pytest.approx(stored_sensor.temperature)
    assert alarm.alarm_type == AlarmType.HIGH
