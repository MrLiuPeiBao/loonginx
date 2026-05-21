from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from app.db.models import RFIDData
from app.services.latest_cache import LatestCache


@dataclass
class RFIDSnapshot:
    id: int
    timestamp: datetime
    device_id: str
    card_id: str
    raw_data: str
    length: Optional[int]
    location: str


_cache = LatestCache[RFIDSnapshot]()


def set_latest_rfid(entity: RFIDData) -> None:
    if getattr(entity, 'id', None) is None:
        return
    _cache.set(
        RFIDSnapshot(
            id=int(entity.id),
            timestamp=entity.timestamp,
            device_id=entity.device_id,
            card_id=entity.card_id,
            raw_data=entity.raw_data,
            length=entity.length,
            location=entity.location,
        )
    )


def get_latest_rfid() -> Optional[RFIDSnapshot]:
    return _cache.get()

