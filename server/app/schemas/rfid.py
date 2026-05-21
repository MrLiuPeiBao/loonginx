from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from sqlmodel import SQLModel


class RFIDDataBase(SQLModel):
    timestamp: datetime
    device_id: str
    card_id: str
    raw_data: str
    length: Optional[int] = None
    location: str


class RFIDDataCreate(RFIDDataBase):
    pass


class RFIDDataRead(RFIDDataBase):
    id: int


class RFIDDataPage(SQLModel):
    total: int
    items: List[RFIDDataRead]

