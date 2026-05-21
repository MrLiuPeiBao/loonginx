"""Cableway PLC API schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from sqlmodel import SQLModel


class CablewayStatusRead(SQLModel):
    """Cableway PLC status snapshot."""

    id: int
    timestamp: datetime
    device_id: str
    location: str
    plc_host: Optional[str] = None
    status: Optional[dict] = None


class CablewayStatusPage(SQLModel):
    """Paginated cableway PLC status response."""

    total: int
    items: List[CablewayStatusRead]


class CablewayCommandRequest(SQLModel):
    """Cableway PLC command request body."""

    type: str
    command_code: Optional[int] = None
    pulse: Optional[bool] = True
    notes: Optional[str] = None
    device_id: Optional[str] = None
    request_id: Optional[str] = None
    params: Optional[Dict[str, float]] = None
