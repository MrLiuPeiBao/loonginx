"""传感器阈值配置模型。"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel


class SensorConfigBase(SQLModel):
    """传感器配置公共字段。"""

    type: str
    version: int = 0
    description: str
    unit: str
    min_threshold: float = 0
    max_threshold: float = 0
    update_time: datetime = Field(default_factory=datetime.now)


class SensorConfigCreate(SensorConfigBase):
    """创建或更新传感器配置。"""

    pass


class SensorConfigRead(SensorConfigBase):
    """读取传感器配置。"""

    pass
