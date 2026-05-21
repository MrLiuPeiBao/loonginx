from __future__ import annotations

from datetime import datetime
from typing import List

from sqlmodel import SQLModel


class MetalAnomalyBase(SQLModel):
    timestamp: datetime
    device_id: str
    location: str


class MetalAnomalyCreate(MetalAnomalyBase):
    pass


class MetalAnomalyRead(MetalAnomalyBase):
    id: int


class MetalAnomalyPage(SQLModel):
    total: int
    items: List[MetalAnomalyRead]

