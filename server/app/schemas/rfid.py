"""RFID 数据模型。"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from sqlmodel import SQLModel


class RFIDDataBase(SQLModel):
    """RFID 数据公共字段。"""

    timestamp: datetime
    device_id: str
    card_id: str
    raw_data: str
    length: Optional[int] = None
    location: str


class RFIDDataCreate(RFIDDataBase):
    """创建 RFID 数据。"""

    pass


class RFIDDataRead(RFIDDataBase):
    """读取 RFID 数据。"""

    id: int


class RFIDDataPage(SQLModel):
    """带总条数的 RFID 分页响应。"""

    total: int
    items: List[RFIDDataRead]
