"""金属异常数据模型。"""

from __future__ import annotations

from datetime import datetime
from typing import List

from sqlmodel import SQLModel


class MetalAnomalyBase(SQLModel):
    """金属异常公共字段。"""

    timestamp: datetime
    device_id: str
    location: str


class MetalAnomalyCreate(MetalAnomalyBase):
    """创建金属异常记录。"""

    pass


class MetalAnomalyRead(MetalAnomalyBase):
    """读取金属异常记录。"""

    id: int


class MetalAnomalyPage(SQLModel):
    """带总条数的金属异常分页响应。"""

    total: int
    items: List[MetalAnomalyRead]
