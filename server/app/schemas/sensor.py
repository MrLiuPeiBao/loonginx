"""传感器相关数据模型。"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from sqlmodel import SQLModel


class SensorDataBase(SQLModel):
    """传感器公共字段。"""

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


class SensorDataCreate(SensorDataBase):
    """创建传感器数据。"""

    pass


class SensorDataRead(SensorDataBase):
    """读取传感器数据。"""

    id: int


class SensorDataPage(SQLModel):
    """带总条数的传感器分页响应。"""

    total: int
    items: List[SensorDataRead]
