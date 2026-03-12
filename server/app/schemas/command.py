"""命令日志与请求模型。"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Union

from sqlmodel import SQLModel

from app.db.models import CommandDirection, CommandStatus


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


class CommandRequestStatusRead(SQLModel):
    """命令请求状态读取模型。"""

    request_id: str
    status: CommandStatus
    command_type: str
    device_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    request_payload: Optional[str] = None
    response_payload: Optional[str] = None
    error: Optional[str] = None
