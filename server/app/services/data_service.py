"""High level data access helpers built on top of SQLModel."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from sqlmodel import Session, select
from sqlalchemy import delete, func, text
from sqlalchemy.orm import load_only

from app.db.models import (
    AlarmRecord,
    AlarmType,
    AudioData,
    BMSData,
    CablewayStatus,
    CommandDirection,
    CommandLog,
    CommandRequestState,
    CommandStatus,
    ImageData,
    MetalAnomaly,
    RFIDData,
    SensorConfig,
    SensorData,
)
from app.core.config import get_settings
from app.services.media_storage import store_audio_bytes, store_image_base64

from .alarm_publisher import build_alarm_event
from .alarm_cache import set_latest_alarm

SENSOR_VALUE_FIELDS: Sequence[str] = (
    'temperature',
    'humidity',
    'pressure',
    'smoke',
    'co',
    'o2',
    'h2s',
    'ch4',
)

RETENTION_TABLES: Dict[str, Tuple[type, object]] = {
    'sensor_data': (SensorData, SensorData.timestamp),
    'bms_data': (BMSData, BMSData.timestamp),
    'rfid_data': (RFIDData, RFIDData.timestamp),
    'cableway_status': (CablewayStatus, CablewayStatus.timestamp),
    'image_data': (ImageData, ImageData.timestamp),
    'audio_data': (AudioData, AudioData.timestamp),
    'command_logs': (CommandLog, CommandLog.timestamp),
    'command_requests': (CommandRequestState, CommandRequestState.updated_at),
}


def _resolve_retention_tables(tables: Optional[Iterable[str]]) -> List[Tuple[type, object]]:
    if not tables:
        return list(RETENTION_TABLES.values())
    resolved: List[Tuple[type, object]] = []
    for name in tables:
        entry = RETENTION_TABLES.get(str(name))
        if entry:
            resolved.append(entry)
    return resolved


def _estimate_db_size_gb() -> float:
    settings = get_settings()
    try:
        from app.db.session import engine

        if engine.dialect.name != 'mysql':
            return 0.0
        statement = text(
            "SELECT ROUND(SUM(data_length + index_length) / 1024 / 1024 / 1024, 2) "
            "FROM information_schema.tables WHERE table_schema = :schema"
        )
        with engine.connect() as conn:
            value = conn.execute(statement, {"schema": settings.mysql_database}).scalar()
        return float(value or 0.0)
    except Exception:
        return 0.0


class DataService:
    """Wrap SQLModel operations with domain specific helpers."""

    def __init__(self, session: Session):
        self.session = session
        self._created_alarm_events: List[Dict[str, Any]] = []
        self._created_alarm_records: List[AlarmRecord] = []

    def consume_alarm_events(self) -> List[Dict[str, Any]]:
        """取出并清空最近一次写入过程中生成的告警事件。

        Returns:
            List[Dict[str, Any]]: 告警事件列表（可直接用于 MQTT 广播发布）。
        """
        events = list(self._created_alarm_events)
        self._created_alarm_events.clear()
        return events

    # ------------------------------------------------------------------
    # Sensor data
    # ------------------------------------------------------------------
    def create_sensor_data(self, items: Iterable[SensorData]) -> List[SensorData]:
        """Persist sensor readings, merging identical timestamps per device/location."""
        self._created_alarm_events.clear()
        self._created_alarm_records.clear()
        unique_entities: List[SensorData] = []
        seen_entities: Set[int] = set()
        evaluation_plan: dict[int, Set[str]] = {}
        entities: dict[int, SensorData] = {}

        for item in items:
            entity, updated_fields = self._upsert_sensor_record(item)
            identity = id(entity)
            if updated_fields:
                entities[identity] = entity
                evaluation_plan.setdefault(identity, set()).update(updated_fields)
            if identity not in seen_entities:
                seen_entities.add(identity)
                unique_entities.append(entity)

        self.session.flush()
        config_map = self._get_sensor_config_map()
        for entity_id, fields in evaluation_plan.items():
            self._evaluate_thresholds(entities[entity_id], fields, config_map=config_map)
        self.session.commit()
        for entity in unique_entities:
            self.session.refresh(entity)
        if self._created_alarm_records:
            latest_alarm = max(
                self._created_alarm_records,
                key=lambda alarm: (alarm.timestamp, alarm.id or 0),
            )
            set_latest_alarm(latest_alarm)
            self._created_alarm_records.clear()
        return unique_entities

    def list_sensor_data(
        self,
        *,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        device_id: Optional[str] = None,
        location: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> List[SensorData]:
        statement = select(SensorData).order_by(SensorData.timestamp.desc())
        if start:
            statement = statement.where(SensorData.timestamp >= start)
        if end:
            statement = statement.where(SensorData.timestamp <= end)
        if device_id:
            statement = statement.where(SensorData.device_id == device_id)
        if location:
            statement = statement.where(SensorData.location == location)
        if limit:
            statement = statement.limit(limit)
        if offset:
            statement = statement.offset(offset)
        return list(self.session.exec(statement))

    def count_sensor_data(
        self,
        *,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        device_id: Optional[str] = None,
        location: Optional[str] = None,
    ) -> int:
        statement = select(func.count()).select_from(SensorData)
        if start:
            statement = statement.where(SensorData.timestamp >= start)
        if end:
            statement = statement.where(SensorData.timestamp <= end)
        if device_id:
            statement = statement.where(SensorData.device_id == device_id)
        if location:
            statement = statement.where(SensorData.location == location)
        return int(self.session.exec(statement).one() or 0)

    def get_latest_sensor_data(
        self,
        *,
        device_id: Optional[str] = None,
        location: Optional[str] = None,
    ) -> Optional[SensorData]:
        # Use insertion order rather than payload timestamp order so fragmented
        # sensor messages arriving out of order still inherit from the last
        # persisted snapshot.
        statement = select(SensorData).order_by(SensorData.id.desc())
        if device_id:
            statement = statement.where(SensorData.device_id == device_id)
        if location:
            statement = statement.where(SensorData.location == location)
        return self.session.exec(statement.limit(1)).first()

    # ------------------------------------------------------------------
    # BMS data
    # ------------------------------------------------------------------
    def create_bms_data(self, items: Iterable[BMSData]) -> List[BMSData]:
        return self._persist_entities(items)

    def list_bms_data(
        self,
        *,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        device_id: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> List[BMSData]:
        statement = select(BMSData).order_by(BMSData.timestamp.desc())
        if start:
            statement = statement.where(BMSData.timestamp >= start)
        if end:
            statement = statement.where(BMSData.timestamp <= end)
        if device_id:
            statement = statement.where(BMSData.device_id == device_id)
        if limit:
            statement = statement.limit(limit)
        if offset:
            statement = statement.offset(offset)
        return list(self.session.exec(statement))

    def count_bms_data(
        self,
        *,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        device_id: Optional[str] = None,
    ) -> int:
        statement = select(func.count()).select_from(BMSData)
        if start:
            statement = statement.where(BMSData.timestamp >= start)
        if end:
            statement = statement.where(BMSData.timestamp <= end)
        if device_id:
            statement = statement.where(BMSData.device_id == device_id)
        return int(self.session.exec(statement).one() or 0)

    # ------------------------------------------------------------------
    # RFID data
    # ------------------------------------------------------------------
    def create_rfid_data(self, items: Iterable[RFIDData]) -> List[RFIDData]:
        return self._persist_entities(items)

    def list_rfid_data(
        self,
        *,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        device_id: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> List[RFIDData]:
        statement = select(RFIDData).order_by(RFIDData.timestamp.desc())
        if start:
            statement = statement.where(RFIDData.timestamp >= start)
        if end:
            statement = statement.where(RFIDData.timestamp <= end)
        if device_id:
            statement = statement.where(RFIDData.device_id == device_id)
        if limit:
            statement = statement.limit(limit)
        if offset:
            statement = statement.offset(offset)
        return list(self.session.exec(statement))

    def count_rfid_data(
        self,
        *,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        device_id: Optional[str] = None,
    ) -> int:
        statement = select(func.count()).select_from(RFIDData)
        if start:
            statement = statement.where(RFIDData.timestamp >= start)
        if end:
            statement = statement.where(RFIDData.timestamp <= end)
        if device_id:
            statement = statement.where(RFIDData.device_id == device_id)
        return int(self.session.exec(statement).one() or 0)

    def get_latest_rfid_card(self) -> Optional[str]:
        """Return the newest RFID card_id if present."""
        row = self.session.exec(
            select(RFIDData.card_id).order_by(RFIDData.timestamp.desc())
        ).first()
        return row

    # ------------------------------------------------------------------
    # Cableway status
    # ------------------------------------------------------------------
    def create_cableway_status(self, items: Iterable[CablewayStatus]) -> List[CablewayStatus]:
        return self._persist_entities(items)

    def list_cableway_status(
        self,
        *,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        device_id: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> List[CablewayStatus]:
        statement = select(CablewayStatus).order_by(CablewayStatus.timestamp.desc())
        if start:
            statement = statement.where(CablewayStatus.timestamp >= start)
        if end:
            statement = statement.where(CablewayStatus.timestamp <= end)
        if device_id:
            statement = statement.where(CablewayStatus.device_id == device_id)
        if limit:
            statement = statement.limit(limit)
        if offset:
            statement = statement.offset(offset)
        return list(self.session.exec(statement))

    def count_cableway_status(
        self,
        *,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        device_id: Optional[str] = None,
    ) -> int:
        statement = select(func.count()).select_from(CablewayStatus)
        if start:
            statement = statement.where(CablewayStatus.timestamp >= start)
        if end:
            statement = statement.where(CablewayStatus.timestamp <= end)
        if device_id:
            statement = statement.where(CablewayStatus.device_id == device_id)
        return int(self.session.exec(statement).one() or 0)

    def get_latest_cableway_status(self, *, device_id: Optional[str] = None) -> Optional[CablewayStatus]:
        statement = select(CablewayStatus).order_by(CablewayStatus.timestamp.desc())
        if device_id:
            statement = statement.where(CablewayStatus.device_id == device_id)
        return self.session.exec(statement.limit(1)).first()

    # ------------------------------------------------------------------
    # Sensor config
    # ------------------------------------------------------------------
    def list_sensor_configs(self) -> List[SensorConfig]:
        statement = select(SensorConfig).order_by(SensorConfig.type)
        return list(self.session.exec(statement))

    def upsert_sensor_config(self, entity: SensorConfig) -> SensorConfig:
        existing = self.session.get(SensorConfig, entity.type)
        if existing:
            for field in (
                'version',
                'description',
                'unit',
                'min_threshold',
                'max_threshold',
                'update_time',
            ):
                setattr(existing, field, getattr(entity, field))
            target = existing
        else:
            self.session.add(entity)
            target = entity

        self.session.commit()
        self.session.refresh(target)
        return target

    def delete_sensor_config(self, sensor_type: str) -> bool:
        existing = self.session.get(SensorConfig, sensor_type)
        if not existing:
            return False
        self.session.delete(existing)
        self.session.commit()
        return True

    # ------------------------------------------------------------------
    # Alarms
    # ------------------------------------------------------------------
    def list_alarm_records(
        self,
        *,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        handled: Optional[bool] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> List[AlarmRecord]:
        statement = select(AlarmRecord).order_by(AlarmRecord.timestamp.desc())
        if start:
            statement = statement.where(AlarmRecord.timestamp >= start)
        if end:
            statement = statement.where(AlarmRecord.timestamp <= end)
        if handled is not None:
            statement = statement.where(AlarmRecord.is_handled == handled)
        if limit:
            statement = statement.limit(limit)
        if offset:
            statement = statement.offset(offset)
        return list(self.session.exec(statement))

    def count_alarm_records(
        self,
        *,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        handled: Optional[bool] = None,
    ) -> int:
        statement = select(func.count()).select_from(AlarmRecord)
        if start:
            statement = statement.where(AlarmRecord.timestamp >= start)
        if end:
            statement = statement.where(AlarmRecord.timestamp <= end)
        if handled is not None:
            statement = statement.where(AlarmRecord.is_handled == handled)
        return int(self.session.exec(statement).one() or 0)

    def create_alarm_record(self, entity: AlarmRecord) -> AlarmRecord:
        self.session.add(entity)
        self.session.flush()
        self.session.commit()
        self.session.refresh(entity)
        set_latest_alarm(entity)
        return entity

    # ------------------------------------------------------------------
    # Command logs
    # ------------------------------------------------------------------
    def add_command_log(
        self,
        *,
        timestamp: datetime,
        direction: CommandDirection,
        payload: str,
        notes: Optional[str] = None,
        device_id: Optional[str] = None,
    ) -> CommandLog:
        entity = CommandLog(
            timestamp=timestamp,
            direction=direction,
            payload=payload,
            notes=notes,
            device_id=device_id,
        )
        self.session.add(entity)
        self.session.flush()
        self.session.commit()
        self.session.refresh(entity)
        return entity

    def list_command_logs(
        self,
        *,
        direction: Optional[CommandDirection] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> List[CommandLog]:
        statement = select(CommandLog).order_by(CommandLog.timestamp.desc())
        if direction:
            statement = statement.where(CommandLog.direction == direction)
        if limit:
            statement = statement.limit(limit)
        if offset:
            statement = statement.offset(offset)
        return list(self.session.exec(statement))

    def count_command_logs(
        self,
        *,
        direction: Optional[CommandDirection] = None,
    ) -> int:
        statement = select(func.count()).select_from(CommandLog)
        if direction:
            statement = statement.where(CommandLog.direction == direction)
        return int(self.session.exec(statement).one() or 0)

    # ------------------------------------------------------------------
    # Command request state machine
    # ------------------------------------------------------------------
    def upsert_command_request(
        self,
        *,
        request_id: str,
        command_type: str,
        device_id: Optional[str],
        request_payload: Optional[str],
        status: CommandStatus = CommandStatus.SENT,
    ) -> CommandRequestState:
        now = datetime.now()
        existing = self.session.get(CommandRequestState, request_id)
        if existing:
            existing.command_type = command_type or existing.command_type
            existing.device_id = device_id or existing.device_id
            if request_payload is not None:
                existing.request_payload = request_payload
            existing.status = status
            existing.updated_at = now
            target = existing
        else:
            target = CommandRequestState(
                request_id=request_id,
                command_type=command_type or 'generic',
                device_id=device_id,
                request_payload=request_payload,
                status=status,
                created_at=now,
                updated_at=now,
            )
            self.session.add(target)

        self.session.commit()
        self.session.refresh(target)
        return target

    def update_command_request_status(
        self,
        *,
        request_id: str,
        status: CommandStatus,
        response_payload: Optional[str] = None,
        error: Optional[str] = None,
        device_id: Optional[str] = None,
    ) -> CommandRequestState:
        now = datetime.now()
        existing = self.session.get(CommandRequestState, request_id)
        if existing is None:
            existing = CommandRequestState(
                request_id=request_id,
                status=status,
                created_at=now,
                updated_at=now,
                device_id=device_id,
                response_payload=response_payload,
                error=error,
            )
            self.session.add(existing)
        else:
            existing.status = status
            existing.updated_at = now
            if response_payload is not None:
                existing.response_payload = response_payload
            if error is not None:
                existing.error = error
            elif status == CommandStatus.ACK:
                existing.error = None
            if device_id:
                existing.device_id = device_id

        self.session.commit()
        self.session.refresh(existing)
        return existing

    def list_command_requests(
        self,
        *,
        status: Optional[CommandStatus] = None,
        device_id: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> List[CommandRequestState]:
        statement = select(CommandRequestState).order_by(CommandRequestState.updated_at.desc())
        if status:
            statement = statement.where(CommandRequestState.status == status)
        if device_id:
            statement = statement.where(CommandRequestState.device_id == device_id)
        if limit:
            statement = statement.limit(limit)
        if offset:
            statement = statement.offset(offset)
        return list(self.session.exec(statement))

    def count_command_requests(
        self,
        *,
        status: Optional[CommandStatus] = None,
        device_id: Optional[str] = None,
    ) -> int:
        statement = select(func.count()).select_from(CommandRequestState)
        if status:
            statement = statement.where(CommandRequestState.status == status)
        if device_id:
            statement = statement.where(CommandRequestState.device_id == device_id)
        return int(self.session.exec(statement).one() or 0)

    def mark_command_timeouts(self, *, timeout_seconds: int) -> int:
        if timeout_seconds <= 0:
            return 0
        cutoff = datetime.now() - timedelta(seconds=timeout_seconds)
        rows = self.session.exec(
            select(CommandRequestState).where(
                CommandRequestState.status == CommandStatus.SENT,
                CommandRequestState.created_at <= cutoff,
            )
        ).all()
        if not rows:
            return 0
        now = datetime.now()
        for row in rows:
            row.status = CommandStatus.TIMEOUT
            row.updated_at = now
            row.error = row.error or 'timeout'
        self.session.commit()
        return len(rows)

    # ------------------------------------------------------------------
    # Media data (image/audio) & metal anomalies
    # ------------------------------------------------------------------
    def create_image_data(self, items: Iterable[ImageData]) -> List[ImageData]:
        settings = get_settings()
        use_fs = settings.media_storage_mode == 'filesystem' and settings.media_store_image
        for item in items:
            if use_fs and item.image_data:
                path = store_image_base64(settings, image_name=item.image_name, image_b64=item.image_data)
                if path:
                    item.image_path = path
                    item.image_data = ''
        return self._persist_entities(items)

    def list_image_data(
        self,
        *,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        device_id: Optional[str] = None,
        location: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        include_data: bool = True,
    ) -> List[ImageData]:
        statement = select(ImageData).order_by(ImageData.timestamp.desc())
        if not include_data:
            statement = statement.options(
                load_only(
                    ImageData.id,
                    ImageData.timestamp,
                    ImageData.device_id,
                    ImageData.image_name,
                    ImageData.location,
                )
            )
        if start:
            statement = statement.where(ImageData.timestamp >= start)
        if end:
            statement = statement.where(ImageData.timestamp <= end)
        if device_id:
            statement = statement.where(ImageData.device_id == device_id)
        if location:
            statement = statement.where(ImageData.location == location)
        if limit:
            statement = statement.limit(limit)
        if offset:
            statement = statement.offset(offset)
        return list(self.session.exec(statement))

    def get_image_data(self, image_id: int) -> Optional[ImageData]:
        return self.session.get(ImageData, image_id)

    def count_image_data(
        self,
        *,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        device_id: Optional[str] = None,
        location: Optional[str] = None,
    ) -> int:
        statement = select(func.count()).select_from(ImageData)
        if start:
            statement = statement.where(ImageData.timestamp >= start)
        if end:
            statement = statement.where(ImageData.timestamp <= end)
        if device_id:
            statement = statement.where(ImageData.device_id == device_id)
        if location:
            statement = statement.where(ImageData.location == location)
        return int(self.session.exec(statement).one() or 0)

    def create_audio_data(self, items: Iterable[AudioData], *, commit: bool = True) -> List[AudioData]:
        settings = get_settings()
        use_fs = settings.media_storage_mode == 'filesystem' and settings.media_store_audio
        for item in items:
            if use_fs and item.audio_data:
                path = store_audio_bytes(settings, audio_name=item.audio_name, audio_bytes=item.audio_data)
                if path:
                    item.audio_path = path
                    item.audio_data = b''
        return self._persist_entities(items, commit=commit)

    def list_audio_data(
        self,
        *,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        device_id: Optional[str] = None,
        location: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        include_data: bool = True,
    ) -> List[AudioData]:
        statement = select(AudioData).order_by(AudioData.timestamp.desc())
        if not include_data:
            statement = statement.options(
                load_only(
                    AudioData.id,
                    AudioData.timestamp,
                    AudioData.device_id,
                    AudioData.audio_name,
                    AudioData.location,
                )
            )
        if start:
            statement = statement.where(AudioData.timestamp >= start)
        if end:
            statement = statement.where(AudioData.timestamp <= end)
        if device_id:
            statement = statement.where(AudioData.device_id == device_id)
        if location:
            statement = statement.where(AudioData.location == location)
        if limit:
            statement = statement.limit(limit)
        if offset:
            statement = statement.offset(offset)
        return list(self.session.exec(statement))

    def get_audio_data(self, audio_id: int) -> Optional[AudioData]:
        return self.session.get(AudioData, audio_id)

    def count_audio_data(
        self,
        *,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        device_id: Optional[str] = None,
        location: Optional[str] = None,
    ) -> int:
        statement = select(func.count()).select_from(AudioData)
        if start:
            statement = statement.where(AudioData.timestamp >= start)
        if end:
            statement = statement.where(AudioData.timestamp <= end)
        if device_id:
            statement = statement.where(AudioData.device_id == device_id)
        if location:
            statement = statement.where(AudioData.location == location)
        return int(self.session.exec(statement).one() or 0)

    def create_metal_anomaly(self, items: Iterable[MetalAnomaly]) -> List[MetalAnomaly]:
        return self._persist_entities(items)

    def list_metal_anomaly(
        self,
        *,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        device_id: Optional[str] = None,
        location: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> List[MetalAnomaly]:
        statement = select(MetalAnomaly).order_by(MetalAnomaly.timestamp.desc())
        if start:
            statement = statement.where(MetalAnomaly.timestamp >= start)
        if end:
            statement = statement.where(MetalAnomaly.timestamp <= end)
        if device_id:
            statement = statement.where(MetalAnomaly.device_id == device_id)
        if location:
            statement = statement.where(MetalAnomaly.location == location)
        if limit:
            statement = statement.limit(limit)
        if offset:
            statement = statement.offset(offset)
        return list(self.session.exec(statement))

    def count_metal_anomaly(
        self,
        *,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        device_id: Optional[str] = None,
        location: Optional[str] = None,
    ) -> int:
        statement = select(func.count()).select_from(MetalAnomaly)
        if start:
            statement = statement.where(MetalAnomaly.timestamp >= start)
        if end:
            statement = statement.where(MetalAnomaly.timestamp <= end)
        if device_id:
            statement = statement.where(MetalAnomaly.device_id == device_id)
        if location:
            statement = statement.where(MetalAnomaly.location == location)
        return int(self.session.exec(statement).one() or 0)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _persist_entities(self, items: Iterable, *, commit: bool = True) -> List:
        entities: List = []
        for item in items:
            self.session.add(item)
            entities.append(item)
        self.session.flush()
        if commit:
            self.session.commit()
        if commit:
            for entity in entities:
                self.session.refresh(entity)
        return entities

    def _find_existing_sensor_record(self, item: SensorData) -> Optional[SensorData]:
        """Return an existing sensor row matching timestamp + device + location."""
        if not item.timestamp or not item.device_id or not item.location:
            return None
        statement = (
            select(SensorData)
            .where(
                SensorData.device_id == item.device_id,
                SensorData.location == item.location,
                SensorData.timestamp == item.timestamp,
            )
            .order_by(SensorData.id.desc())
            .limit(1)
        )
        return self.session.exec(statement).first()

    def _merge_sensor_values(
        self,
        target: SensorData,
        source: SensorData,
    ) -> Set[str]:
        """Merge new sensor values into an existing row and return updated fields."""
        updated: Set[str] = set()
        for field in SENSOR_VALUE_FIELDS:
            new_value = getattr(source, field, None)
            if new_value is None:
                continue
            if getattr(target, field, None) != new_value:
                setattr(target, field, new_value)
                updated.add(field)
        return updated

    def _upsert_sensor_record(
        self,
        item: SensorData,
    ) -> Tuple[SensorData, Set[str]]:
        """Insert a brand new sensor record or merge into an existing one."""
        existing = self._find_existing_sensor_record(item)
        if existing:
            updated_fields = self._merge_sensor_values(existing, item)
            return existing, updated_fields

        self.session.add(item)
        updated_fields = {
            field
            for field in SENSOR_VALUE_FIELDS
            if getattr(item, field, None) is not None
        }
        return item, updated_fields

    def _evaluate_thresholds(
        self,
        sensor: SensorData,
        fields: Optional[Iterable[str]] = None,
        *,
        config_map: Optional[dict[str, SensorConfig]] = None,
    ) -> None:
        """Create alarm records when selected fields violate configured thresholds."""
        target_fields = list(fields) if fields else list(SENSOR_VALUE_FIELDS)
        location = sensor.location or ''
        for field in target_fields:
            if field not in SENSOR_VALUE_FIELDS:
                continue
            value = getattr(sensor, field, None)
            if value is None:
                continue

            config = (config_map or {}).get(field) or self.session.get(SensorConfig, field)
            if not config:
                continue

            min_threshold = config.min_threshold
            max_threshold = config.max_threshold
            alarm_type: Optional[AlarmType] = None

            if min_threshold is not None and value < min_threshold:
                alarm_type = AlarmType.LOW
            elif max_threshold is not None and value > max_threshold:
                alarm_type = AlarmType.HIGH

            if alarm_type is None:
                continue

            already_exists = self.session.exec(
                select(AlarmRecord.id).where(
                    AlarmRecord.sensor_key == field,
                    AlarmRecord.timestamp == sensor.timestamp,
                    AlarmRecord.location == location,
                )
            ).first()
            if already_exists:
                continue

            alarm = AlarmRecord(
                timestamp=sensor.timestamp,
                sensor_key=field,
                sensor_name=config.description or field,
                value=value,
                unit=config.unit or '',
                min_threshold=min_threshold,
                max_threshold=max_threshold,
                alarm_type=alarm_type,
                is_handled=False,
                location=location,
            )
            self.session.add(alarm)
            self._created_alarm_records.append(alarm)
            self._created_alarm_events.append(
                build_alarm_event(
                    source='sensor_threshold',
                    timestamp=sensor.timestamp,
                    device_id=sensor.device_id or None,
                    location=location,
                    payload={
                        'sensor_key': field,
                        'sensor_name': config.description or field,
                        'value': float(value),
                        'unit': config.unit or '',
                        'min_threshold': float(min_threshold) if min_threshold is not None else 0.0,
                        'max_threshold': float(max_threshold) if max_threshold is not None else 0.0,
                        'alarm_type': alarm_type.value,
                    },
                )
            )

    def _get_sensor_config_map(self) -> dict[str, SensorConfig]:
        """Preload sensor configs to avoid repetitive DB lookups per field."""
        configs = self.session.exec(select(SensorConfig)).all()
        return {config.type: config for config in configs}

    # ------------------------------------------------------------------
    # Data retention
    # ------------------------------------------------------------------
    def prune_old_records(self, *, days: int, tables: Optional[Iterable[str]] = None) -> Dict[str, int]:
        if days <= 0:
            return {'deleted': 0}
        cutoff = datetime.now() - timedelta(days=days)
        deleted = 0
        for model, time_column in _resolve_retention_tables(tables):
            result = self.session.exec(delete(model).where(time_column < cutoff))
            deleted += int(result.rowcount or 0)
        self.session.commit()
        return {'deleted': deleted}

    def prune_by_size(
        self,
        *,
        max_gb: float,
        tables: Optional[Iterable[str]] = None,
        batch: int = 1000,
    ) -> Dict[str, int]:
        if max_gb <= 0:
            return {'deleted': 0}
        deleted = 0
        size_gb = _estimate_db_size_gb()
        while size_gb > max_gb:
            deleted_this_round = 0
            for model, time_column in _resolve_retention_tables(tables):
                rows = self.session.exec(
                    select(model).order_by(time_column.asc()).limit(batch)
                ).all()
                for row in rows:
                    self.session.delete(row)
                deleted_this_round += len(rows)
            if deleted_this_round == 0:
                break
            deleted += deleted_this_round
            self.session.commit()
            size_gb = _estimate_db_size_gb()
        return {'deleted': deleted}
