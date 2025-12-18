"""SQLModel ORM 定义。"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional

from sqlmodel import Field, SQLModel
from sqlalchemy import Column, Index, LargeBinary, UniqueConstraint, JSON
from sqlalchemy.dialects.mysql import LONGBLOB

AUDIO_DATA_STORAGE_TYPE = LargeBinary().with_variant(LONGBLOB(), 'mysql')


class SensorTypeEnum(str, Enum):
    """传感器类型枚举。"""

    TEMPERATURE = 'temperature'
    HUMIDITY = 'humidity'
    PRESSURE = 'pressure'
    SMOKE = 'smoke'
    CO = 'co'
    O2 = 'o2'
    H2S = 'h2s'
    CH4 = 'ch4'
    UNKNOWN = 'unknown'


class SensorConfig(SQLModel, table=True):
    """传感器阈值配置。"""

    __tablename__ = 'sensor_config'

    type: str = Field(primary_key=True, max_length=255)
    version: int = Field(default=0, ge=0)
    description: str = Field(max_length=255)
    unit: str = Field(max_length=255)
    min_threshold: float = Field(default=0)
    max_threshold: float = Field(default=0)
    update_time: datetime = Field(default_factory=datetime.utcnow)


class SensorData(SQLModel, table=True):
    """环境传感器数据。"""

    __tablename__ = 'sensor_data'
    __table_args__ = (
        Index('idx_sensor_device_location', 'device_id', 'location'),
        Index('idx_sensor_timestamp', 'timestamp'),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    timestamp: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    device_id: str = Field(max_length=50, nullable=False)
    location: str = Field(max_length=255, nullable=False)
    temperature: Optional[float] = None
    humidity: Optional[float] = None
    smoke: Optional[float] = None
    co: Optional[float] = None
    o2: Optional[float] = None
    h2s: Optional[float] = None
    ch4: Optional[float] = None
    pressure: Optional[float] = None


class AlarmType(str, Enum):
    """报警类型。"""

    LOW = 'low'
    HIGH = 'high'


class AlarmRecord(SQLModel, table=True):
    """报警记录。"""

    __tablename__ = 'alarm_records'

    id: Optional[int] = Field(default=None, primary_key=True)
    timestamp: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    sensor_key: str = Field(max_length=20, nullable=False)
    sensor_name: str = Field(max_length=50, nullable=False)
    value: float = Field(nullable=False)
    unit: str = Field(max_length=10, nullable=False)
    min_threshold: float = Field(nullable=False)
    max_threshold: float = Field(nullable=False)
    alarm_type: AlarmType = Field(nullable=False)
    is_handled: bool = Field(default=False, nullable=False)
    location: str = Field(max_length=255, nullable=False)


class ImageData(SQLModel, table=True):
    """行人截图数据。"""

    __tablename__ = 'image_data'

    id: Optional[int] = Field(default=None, primary_key=True)
    timestamp: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    device_id: str = Field(max_length=50, nullable=False)
    image_name: str = Field(max_length=100, nullable=False)
    image_data: str = Field(nullable=False)
    location: str = Field(max_length=255, nullable=False)


class AudioData(SQLModel, table=True):
    """音频数据。"""

    __tablename__ = 'audio_data'

    id: Optional[int] = Field(default=None, primary_key=True)
    timestamp: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    device_id: str = Field(max_length=50, nullable=False)
    audio_name: str = Field(max_length=100, nullable=False)
    audio_data: bytes = Field(sa_column=Column(AUDIO_DATA_STORAGE_TYPE, nullable=False))
    location: str = Field(max_length=255, nullable=False)


class MetalAnomaly(SQLModel, table=True):
    """金属异常信息。"""

    __tablename__ = 'metal_anomaly'

    id: Optional[int] = Field(default=None, primary_key=True)
    timestamp: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    device_id: str = Field(max_length=50, nullable=False)
    location: str = Field(max_length=100, nullable=False)


class BMSData(SQLModel, table=True):
    """BMS 数据。"""

    __tablename__ = 'bms_data'
    __table_args__ = (Index('idx_bms_device', 'device_id', 'timestamp'),)

    id: Optional[int] = Field(default=None, primary_key=True)
    timestamp: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    device_id: str = Field(max_length=50, nullable=False)
    location: str = Field(max_length=255, nullable=False)
    voltage: Optional[float] = None
    soc: Optional[float] = None
    status: Optional[int] = None
    capacity: Optional[float] = None
    power: Optional[float] = None
    current: Optional[float] = None
    cell_voltages: Optional[List[float]] = Field(
        default=None,
        sa_column=Column(JSON),
    )


class RFIDData(SQLModel, table=True):
    """RFID 读卡数据。"""

    __tablename__ = 'rfid_data'
    __table_args__ = (Index('idx_rfid_timestamp', 'timestamp'),)

    id: Optional[int] = Field(default=None, primary_key=True)
    timestamp: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    device_id: str = Field(max_length=50, nullable=False)
    card_id: str = Field(max_length=255, nullable=False)
    raw_data: str = Field(nullable=False)
    length: Optional[int] = None
    location: str = Field(max_length=255, nullable=False)


class CommandDirection(str, Enum):
    """命令方向。"""

    REQUEST = 'request'
    RESPONSE = 'response'


class CommandLog(SQLModel, table=True):
    """命令历史。"""

    __tablename__ = 'command_logs'
    __table_args__ = (UniqueConstraint('timestamp', 'payload', name='uq_command_payload'),)

    id: Optional[int] = Field(default=None, primary_key=True)
    timestamp: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    direction: CommandDirection = Field(nullable=False)
    payload: str = Field(nullable=False)
    notes: Optional[str] = Field(default=None, max_length=255)
    device_id: Optional[str] = Field(default=None, max_length=50)
