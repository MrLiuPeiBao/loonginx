from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from app.db.models import SensorData
from app.services.latest_cache import LatestCache


@dataclass
class SensorSnapshot:
    id: int
    timestamp: datetime
    device_id: str
    location: str
    temperature: Optional[float] = None
    humidity: Optional[float] = None
    smoke: Optional[float] = None
    co: Optional[float] = None
    o2: Optional[float] = None
    h2s: Optional[float] = None
    ch4: Optional[float] = None
    pressure: Optional[float] = None


_cache = LatestCache[SensorSnapshot]()


def set_latest_sensor(entity: SensorData) -> None:
    if getattr(entity, 'id', None) is None:
        return
    _cache.set(
        SensorSnapshot(
            id=int(entity.id),
            timestamp=entity.timestamp,
            device_id=entity.device_id,
            location=entity.location,
            temperature=entity.temperature,
            humidity=entity.humidity,
            smoke=entity.smoke,
            co=entity.co,
            o2=entity.o2,
            h2s=entity.h2s,
            ch4=entity.ch4,
            pressure=entity.pressure,
        )
    )


def get_latest_sensor() -> Optional[SensorSnapshot]:
    return _cache.get()

