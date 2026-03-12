"""Unit tests for MQTT ingestion helpers."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.services import ingestion
from app.services.data_service import SENSOR_VALUE_FIELDS


def _build_bucket(
    base_time: datetime,
    device_id: str,
    location: str,
    start_value: float,
) -> list[dict]:
    """Construct a complete sensor bucket with distinct values."""
    items: list[dict] = []
    for index, field in enumerate(SENSOR_VALUE_FIELDS):
        items.append(
            {
                "sensor_type": field,
                "value": start_value + index,
                "timestamp": base_time + timedelta(seconds=index * 0.1),
                "device_id": device_id,
                "location": location,
            }
        )
    return items


def test_parse_sensor_message_groups_by_bucket_and_coerces_values() -> None:
    """List payloads are grouped into 2s buckets with latest timestamp retained."""
    base_time = datetime(2024, 1, 1, 12, 0, 0)
    bucket_one = _build_bucket(base_time, "dev-1", "zone-a", start_value=1.0)
    bucket_two = _build_bucket(base_time + timedelta(seconds=3), "dev-1", "zone-a", start_value=10.0)
    message = bucket_one + bucket_two

    records = ingestion._parse_sensor_message(
        message,
        default_device_id="fallback",
        default_location="fallback",
    )

    assert len(records) == 2

    first, second = records
    assert first.device_id == "dev-1"
    assert first.location == "zone-a"
    assert first.timestamp == bucket_one[-1]["timestamp"]
    for item in bucket_one:
        assert getattr(first, item["sensor_type"]) == pytest.approx(item["value"])

    assert second.timestamp == bucket_two[-1]["timestamp"]
    for item in bucket_two:
        assert getattr(second, item["sensor_type"]) == pytest.approx(item["value"])


def test_parse_sensor_message_discards_incomplete_dict_payload() -> None:
    """Dict payload missing fields is ignored to avoid partial rows."""
    message = {
        "sensors": {
            "temperature": "20.5",
            "humidity": "30.1",
        },
        "timestamp": datetime(2024, 1, 1, 0, 0, 0).isoformat(),
        "device_id": "dev-3",
        "location": "zone-b",
    }

    records = ingestion._parse_sensor_message(message)

    assert records == []
