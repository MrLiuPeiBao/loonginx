from __future__ import annotations

from datetime import datetime

from sqlmodel import SQLModel


class SensorConfigBase(SQLModel):
    type: str
    version: int = 0
    description: str
    unit: str
    min_threshold: float = 0.0
    max_threshold: float = 0.0
    update_time: datetime


class SensorConfigCreate(SensorConfigBase):
    pass


class SensorConfigRead(SensorConfigBase):
    pass

