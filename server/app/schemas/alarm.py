"""报警记录模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import SQLModel

from app.db.models import AlarmType


class AlarmRecordRead(SQLModel):
    """报警记录读取模型。"""

    id: int
    timestamp: datetime
    sensor_key: str
    sensor_name: str
    value: float
    unit: str
    min_threshold: float
    max_threshold: float
    alarm_type: AlarmType
    is_handled: bool
    location: str


class AlarmRecordCreate(SQLModel):
    """报警记录创建模型。"""

    timestamp: Optional[datetime] = None
    device_id: Optional[str] = None
    sensor_key: str
    sensor_name: str
    value: float
    unit: str
    min_threshold: float
    max_threshold: float
    alarm_type: AlarmType
    is_handled: bool = False
    location: str = ''
