from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from sqlmodel import SQLModel


class CommandLogRead(SQLModel):
    id: int
    timestamp: datetime
    direction: str
    payload: str
    notes: Optional[str] = None
    device_id: Optional[str] = None


class CommandLogPage(SQLModel):
    total: int
    items: List[CommandLogRead]


class CommandRequest(SQLModel):
    payload: str
    notes: Optional[str] = None
    device_id: Optional[str] = None
    request_id: Optional[str] = None


class CommandRequestStatusRead(SQLModel):
    request_id: str
    created_at: datetime
    updated_at: datetime
    command_type: str
    status: str
    device_id: Optional[str] = None
    request_payload: Optional[str] = None
    response_payload: Optional[str] = None
    error: Optional[str] = None


class CommandRequestStatusPage(SQLModel):
    total: int
    items: List[CommandRequestStatusRead]

