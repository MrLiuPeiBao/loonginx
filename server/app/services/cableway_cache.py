from __future__ import annotations

from typing import Optional

from app.db.models import CablewayStatus
from app.services.latest_cache import LatestCache


_cache = LatestCache[dict]()


def set_latest_cableway_status(entity: CablewayStatus) -> None:
    if getattr(entity, 'id', None) is None:
        return
    data = entity.dict()
    data['id'] = int(entity.id)
    _cache.set(data)


def get_latest_cableway_status() -> Optional[dict]:
    return _cache.get()
