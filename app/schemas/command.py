"""命令日志与请求模型。"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Union

from sqlmodel import SQLModel

from app.db.models import CommandDirection


class CommandLogRead(SQLModel):
    """命令日志读取模型。"""

    id: int
    timestamp: datetime
    direction: CommandDirection
    payload: str
    notes: Optional[str] = None
    device_id: Optional[str] = None


class CommandRequest(SQLModel):
    """命令发送请求。"""

    payload: Union[str, List[int]]
    notes: Optional[str] = None
    device_id: Optional[str] = None
    request_id: Optional[str] = None
