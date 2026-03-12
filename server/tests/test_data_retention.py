from __future__ import annotations

from datetime import datetime, timedelta

from sqlmodel import select

from app.db.models import SensorData
from app.services.data_service import DataService


def _create_sensor(ts: datetime) -> SensorData:
    return SensorData(
        timestamp=ts,
        device_id="dev-1",
        location="loc",
    )


def test_prune_old_records_deletes_by_days(session) -> None:
    service = DataService(session)
    now = datetime.now()
    old = _create_sensor(now - timedelta(days=2))
    fresh = _create_sensor(now)
    session.add(old)
    session.add(fresh)
    session.commit()

    result = service.prune_old_records(days=1, tables=["sensor_data"])

    remaining = session.exec(select(SensorData)).all()
    assert len(remaining) == 1
    assert remaining[0].timestamp == fresh.timestamp
    assert result["deleted"] >= 1


def test_prune_by_size_removes_oldest_batch(session, monkeypatch) -> None:
    service = DataService(session)
    now = datetime.now()
    oldest = _create_sensor(now - timedelta(minutes=10))
    newest = _create_sensor(now)
    session.add(oldest)
    session.add(newest)
    session.commit()

    size_sequence = [2.0, 0.5]

    def fake_size_gb():
        return size_sequence.pop(0)

    monkeypatch.setattr("app.services.data_service._estimate_db_size_gb", fake_size_gb)

    result = service.prune_by_size(max_gb=1.0, tables=["sensor_data"], batch=1)

    remaining = session.exec(select(SensorData)).all()
    assert len(remaining) == 1
    assert remaining[0].timestamp == newest.timestamp
    assert result["deleted"] >= 1
