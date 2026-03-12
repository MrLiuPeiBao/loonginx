from __future__ import annotations

from typing import Optional

from app.db.models import BMSData
from app.services.latest_cache import LatestCache


_cache = LatestCache[dict]()


def set_latest_bms(entity: BMSData) -> None:
    if getattr(entity, 'id', None) is None:
        return
    data = entity.dict()
    data['id'] = int(entity.id)
    _cache.set(data)


def get_latest_bms() -> Optional[dict]:
    return _cache.get()
