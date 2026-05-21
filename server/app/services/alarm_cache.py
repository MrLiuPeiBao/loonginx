from __future__ import annotations

from typing import Optional

from app.db.models import AlarmRecord
from app.services.latest_cache import LatestCache


_cache = LatestCache[dict]()


def set_latest_alarm(entity: AlarmRecord) -> None:
    if getattr(entity, 'id', None) is None:
        return
    data = entity.model_dump()
    data['id'] = int(entity.id)
    _cache.set(data)


def get_latest_alarm() -> Optional[dict]:
    return _cache.get()
