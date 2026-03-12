"""索道 PLC（Modbus TCP）相关 API 模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, Optional

from sqlmodel import SQLModel


class CablewayStatusRead(SQLModel):
    """索道 PLC 状态读取模型。"""

    id: int
    timestamp: datetime
    device_id: str
    location: str
    plc_host: Optional[str] = None
    status: Optional[dict] = None


class CablewayCommandRequest(SQLModel):
    """向索道 PLC 发送指令/参数（通过 MQTT 转发到网关）。"""

    type: str
    command_code: Optional[int] = None
    params: Optional[Dict[str, float]] = None
    pulse: Optional[bool] = True
    notes: Optional[str] = None
    device_id: Optional[str] = None
    request_id: Optional[str] = None
