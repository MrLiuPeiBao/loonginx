from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from app.db.models import BMSData
from app.services.latest_cache import LatestCache


@dataclass
class BMSSnapshot:
    id: int
    timestamp: datetime
    device_id: str
    location: str
    voltage: Optional[float] = None
    soc: Optional[float] = None
    status: Optional[int] = None
    capacity: Optional[float] = None
    power: Optional[float] = None
    current: Optional[float] = None
    cell_voltages: Optional[List[float]] = None


_cache = LatestCache[BMSSnapshot]()


def set_latest_bms(entity: BMSData) -> None:
    if getattr(entity, 'id', None) is None:
        return
    _cache.set(
        BMSSnapshot(
            id=int(entity.id),
            timestamp=entity.timestamp,
            device_id=entity.device_id,
            location=entity.location,
            voltage=entity.voltage,
            soc=entity.soc,
            status=entity.status,
            capacity=entity.capacity,
            power=entity.power,
            current=entity.current,
            cell_voltages=entity.cell_voltages,
        )
    )


def get_latest_bms() -> Optional[BMSSnapshot]:
    return _cache.get()

