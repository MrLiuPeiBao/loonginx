from __future__ import annotations

from datetime import datetime
from typing import List

from sqlmodel import SQLModel


class AlarmRecordBase(SQLModel):
    timestamp: datetime
    sensor_key: str
    sensor_name: str
    value: float
    unit: str
    min_threshold: float
    max_threshold: float
    alarm_type: str
    is_handled: bool = False
    location: str


class AlarmRecordCreate(AlarmRecordBase):
    pass


class AlarmRecordRead(AlarmRecordBase):
    id: int


class AlarmRecordPage(SQLModel):
    total: int
    items: List[AlarmRecordRead]

