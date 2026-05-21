from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from sqlmodel import SQLModel


class BMSDataBase(SQLModel):
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


class BMSDataCreate(BMSDataBase):
    pass


class BMSDataRead(BMSDataBase):
    id: int


class BMSDataPage(SQLModel):
    total: int
    items: List[BMSDataRead]

