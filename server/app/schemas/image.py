"""图像数据模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import SQLModel


class ImageDataBase(SQLModel):
    """图像数据公共字段。"""

    timestamp: datetime
    device_id: str
    image_name: str
    image_data: str
    location: str


class ImageDataCreate(ImageDataBase):
    """创建图像数据。"""

    pass


class ImageDataRead(ImageDataBase):
    """读取图像数据。"""

    id: int
