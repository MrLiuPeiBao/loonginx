from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from sqlmodel import SQLModel


class SensorDataBase(SQLModel):
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
    pass


class SensorDataRead(SensorDataBase):
    id: int


class SensorDataPage(SQLModel):
    total: int
    items: List[SensorDataRead]

