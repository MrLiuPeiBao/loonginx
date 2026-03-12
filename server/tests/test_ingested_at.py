from __future__ import annotations

from datetime import datetime

from app.db.models import BMSData, SensorData
from app.services.data_service import DataService


def test_timestamp_set_for_sensor_data(session) -> None:
    service = DataService(session)
    record = SensorData(
        timestamp=datetime(2024, 1, 1, 0, 0, 0),
        device_id="dev-1",
        location="lab",
        temperature=20.0,
        humidity=50.0,
    )

    result = service.create_sensor_data([record])

    assert result[0].timestamp is not None


def test_timestamp_set_for_bms_data(session) -> None:
    service = DataService(session)
    record = BMSData(
        timestamp=datetime(2024, 1, 1, 0, 0, 0),
        device_id="dev-1",
        location="lab",
        voltage=12.5,
    )

    result = service.create_bms_data([record])

    assert result[0].timestamp is not None
