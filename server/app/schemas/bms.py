"""BMS 数据模型。"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from sqlmodel import SQLModel


class BMSDataBase(SQLModel):
    """BMS 公共字段。"""

    timestamp: datetime
    device_id: str
    location: str
    voltage: Optional[float] = None
    soc: Optional[float] = None
    status: Optional[int] = None
    capacity: Optional[float] = None
    power: Optional[float] = None
    current: Optional[float] = None
    cell_voltages: Optional[List[Optional[float]]] = None


class BMSDataCreate(BMSDataBase):
    """创建 BMS 数据。"""

    pass


class BMSDataRead(BMSDataBase):
    """读取 BMS 数据。"""

    id: int


class BMSDataPage(SQLModel):
    """带总条数的 BMS 分页响应。"""

    total: int
    items: List[BMSDataRead]
