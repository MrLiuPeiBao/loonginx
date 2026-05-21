"""音频数据模型。"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from sqlmodel import SQLModel


class AudioDataBase(SQLModel):
    """音频数据公共字段。"""

    timestamp: datetime
    device_id: str
    audio_name: str
    audio_data: str
    location: str


class AudioDataCreate(AudioDataBase):
    """创建音频数据。"""

    pass


class AudioDataRead(AudioDataBase):
    """读取音频数据。"""

    id: int


class AudioDataPage(SQLModel):
    """带总条数的音频分页响应。"""

    status: str = 'ok'
    total: int
    items: List[AudioDataRead]
