"""SQLModel ORM definitions."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional

from sqlalchemy import JSON, Column, Index, LargeBinary, Text, UniqueConstraint
from sqlalchemy.dialects.mysql import LONGBLOB, LONGTEXT
from sqlmodel import Field, SQLModel

AUDIO_DATA_STORAGE_TYPE = LargeBinary().with_variant(LONGBLOB(), 'mysql')
CONFIG_JSON_STORAGE_TYPE = Text().with_variant(LONGTEXT(), 'mysql')
COMMAND_TEXT_STORAGE_TYPE = Text().with_variant(LONGTEXT(), 'mysql')
IMAGE_DATA_STORAGE_TYPE = Text().with_variant(LONGTEXT(), 'mysql')


class SensorTypeEnum(str, Enum):
    """Supported sensor types."""

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
    """Sensor threshold configuration."""

    __tablename__ = 'sensor_config'

    type: str = Field(primary_key=True, max_length=255)
    version: int = Field(default=0, ge=0)
    description: str = Field(max_length=255)
    unit: str = Field(max_length=255)
    min_threshold: float = Field(default=0)
    max_threshold: float = Field(default=0)
    update_time: datetime = Field(default_factory=datetime.now)


class RuntimeConfig(SQLModel, table=True):
    """Runtime override storage."""

    __tablename__ = 'runtime_config'

    id: Optional[int] = Field(default=None, primary_key=True)
    updated_at: datetime = Field(default_factory=datetime.now, nullable=False)
    config_json: str = Field(default='{}', sa_column=Column(CONFIG_JSON_STORAGE_TYPE))


class SensorData(SQLModel, table=True):
    """Environmental sensor readings."""

    __tablename__ = 'sensor_data'
    __table_args__ = (
        Index('idx_sensor_device_location', 'device_id', 'location'),
        Index('idx_sensor_timestamp', 'timestamp'),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    timestamp: datetime = Field(default_factory=datetime.now, nullable=False)
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
    """Alarm type."""

    LOW = 'low'
    HIGH = 'high'


class AlarmRecord(SQLModel, table=True):
    """Alarm record."""

    __tablename__ = 'alarm_records'

    id: Optional[int] = Field(default=None, primary_key=True)
    timestamp: datetime = Field(default_factory=datetime.now, nullable=False)
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
    """Captured image data."""

    __tablename__ = 'image_data'
    __table_args__ = (Index('idx_image_timestamp', 'timestamp'),)

    id: Optional[int] = Field(default=None, primary_key=True)
    timestamp: datetime = Field(default_factory=datetime.now, nullable=False)
    device_id: str = Field(max_length=50, nullable=False)
    image_name: str = Field(max_length=100, nullable=False)
    image_data: str = Field(sa_column=Column(IMAGE_DATA_STORAGE_TYPE, nullable=False))
    location: str = Field(max_length=255, nullable=False)
    image_path: Optional[str] = Field(default=None, max_length=512)


class AudioData(SQLModel, table=True):
    """Captured audio data."""

    __tablename__ = 'audio_data'
    __table_args__ = (Index('idx_audio_timestamp', 'timestamp'),)

    id: Optional[int] = Field(default=None, primary_key=True)
    timestamp: datetime = Field(default_factory=datetime.now, nullable=False)
    device_id: str = Field(max_length=50, nullable=False)
    audio_name: str = Field(max_length=100, nullable=False)
    audio_data: bytes = Field(sa_column=Column(AUDIO_DATA_STORAGE_TYPE, nullable=False))
    location: str = Field(max_length=255, nullable=False)
    audio_path: Optional[str] = Field(default=None, max_length=512)


class MetalAnomaly(SQLModel, table=True):
    """Metal anomaly event."""

    __tablename__ = 'metal_anomaly'

    id: Optional[int] = Field(default=None, primary_key=True)
    timestamp: datetime = Field(default_factory=datetime.now, nullable=False)
    device_id: str = Field(max_length=50, nullable=False)
    location: str = Field(max_length=100, nullable=False)


class BMSData(SQLModel, table=True):
    """BMS data."""

    __tablename__ = 'bms_data'
    __table_args__ = (Index('idx_bms_device', 'device_id', 'timestamp'),)

    id: Optional[int] = Field(default=None, primary_key=True)
    timestamp: datetime = Field(default_factory=datetime.now, nullable=False)
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
    """RFID readings."""

    __tablename__ = 'rfid_data'
    __table_args__ = (Index('idx_rfid_timestamp', 'timestamp'),)

    id: Optional[int] = Field(default=None, primary_key=True)
    timestamp: datetime = Field(default_factory=datetime.now, nullable=False)
    device_id: str = Field(max_length=50, nullable=False)
    card_id: str = Field(max_length=255, nullable=False)
    raw_data: str = Field(nullable=False)
    length: Optional[int] = None
    location: str = Field(max_length=255, nullable=False)


class CablewayStatus(SQLModel, table=True):
    """Cableway PLC status snapshots."""

    __tablename__ = 'cableway_status'
    __table_args__ = (
        Index('idx_cableway_device', 'device_id', 'timestamp'),
        Index('idx_cableway_timestamp', 'timestamp'),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    timestamp: datetime = Field(default_factory=datetime.now, nullable=False)
    device_id: str = Field(max_length=50, nullable=False)
    location: str = Field(max_length=255, nullable=False, default='')
    plc_host: Optional[str] = Field(default=None, max_length=50)
    status: Optional[dict] = Field(default=None, sa_column=Column(JSON))


class CommandDirection(str, Enum):
    """Command direction."""

    REQUEST = 'request'
    RESPONSE = 'response'


class CommandLog(SQLModel, table=True):
    """Command history."""

    __tablename__ = 'command_logs'
    __table_args__ = (
        UniqueConstraint('timestamp', 'payload', name='uq_command_payload'),
        Index('idx_command_timestamp', 'timestamp'),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    timestamp: datetime = Field(default_factory=datetime.now, nullable=False)
    direction: CommandDirection = Field(nullable=False)
    payload: str = Field(max_length=700, nullable=False)
    notes: Optional[str] = Field(default=None, max_length=255)
    device_id: Optional[str] = Field(default=None, max_length=50)


class CommandStatus(str, Enum):
    """Command request status."""

    SENT = 'sent'
    ACK = 'ack'
    FAILED = 'failed'
    TIMEOUT = 'timeout'


class CommandRequestState(SQLModel, table=True):
    """Track command request lifecycle by request_id."""

    __tablename__ = 'command_requests'
    __table_args__ = (
        Index('idx_command_status', 'status', 'updated_at'),
        Index('idx_command_device_status', 'device_id', 'status'),
    )

    request_id: str = Field(primary_key=True, max_length=64)
    created_at: datetime = Field(default_factory=datetime.now, nullable=False)
    updated_at: datetime = Field(default_factory=datetime.now, nullable=False)
    command_type: str = Field(default='generic', max_length=32)
    status: CommandStatus = Field(default=CommandStatus.SENT, nullable=False)
    device_id: Optional[str] = Field(default=None, max_length=50)
    request_payload: Optional[str] = Field(default=None, sa_column=Column(COMMAND_TEXT_STORAGE_TYPE))
    response_payload: Optional[str] = Field(default=None, sa_column=Column(COMMAND_TEXT_STORAGE_TYPE))
    error: Optional[str] = Field(default=None, max_length=255)


class DeviceHelloState(SQLModel, table=True):
    """Latest device hello handshake state."""

    __tablename__ = 'device_hello_state'

    device_id: str = Field(primary_key=True, max_length=64)
    config_version: Optional[int] = Field(default=None)
    payload_json: str = Field(default='{}', sa_column=Column(CONFIG_JSON_STORAGE_TYPE))
    seen_at: datetime = Field(default_factory=datetime.now, nullable=False)


class ConfigAckState(SQLModel, table=True):
    """Latest config ack state."""

    __tablename__ = 'config_ack_state'

    device_id: str = Field(primary_key=True, max_length=64)
    config_version: Optional[int] = Field(default=None)
    pending_restart_keys: Optional[str] = Field(default=None, sa_column=Column(COMMAND_TEXT_STORAGE_TYPE))
    payload_json: str = Field(default='{}', sa_column=Column(CONFIG_JSON_STORAGE_TYPE))
    seen_at: datetime = Field(default_factory=datetime.now, nullable=False)
