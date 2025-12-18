"""High level data access helpers built on top of SQLModel."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from sqlmodel import Session, select

from app.db.models import (
    AlarmRecord,
    AlarmType,
    AudioData,
    BMSData,
    CommandDirection,
    CommandLog,
    ImageData,
    MetalAnomaly,
    RFIDData,
    SensorConfig,
    SensorData,
)

from .alarm_publisher import build_alarm_event

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


class DataService:
    """Wrap SQLModel operations with domain specific helpers."""

    def __init__(self, session: Session):
        self.session = session
        self._created_alarm_events: List[Dict[str, Any]] = []

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

    def get_latest_rfid_card(self) -> Optional[str]:
        """Return the newest RFID card_id if present."""
        row = self.session.exec(
            select(RFIDData.card_id).order_by(RFIDData.timestamp.desc())
        ).first()
        return row

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

    def create_alarm_record(self, entity: AlarmRecord) -> AlarmRecord:
        self.session.add(entity)
        self.session.flush()
        self.session.commit()
        self.session.refresh(entity)
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

    # ------------------------------------------------------------------
    # Media data (image/audio) & metal anomalies
    # ------------------------------------------------------------------
    def create_image_data(self, items: Iterable[ImageData]) -> List[ImageData]:
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
    ) -> List[ImageData]:
        statement = select(ImageData).order_by(ImageData.timestamp.desc())
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

    def create_audio_data(self, items: Iterable[AudioData]) -> List[AudioData]:
        return self._persist_entities(items)

    def list_audio_data(
        self,
        *,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        device_id: Optional[str] = None,
        location: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> List[AudioData]:
        statement = select(AudioData).order_by(AudioData.timestamp.desc())
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

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _persist_entities(self, items: Iterable) -> List:
        entities: List = []
        for item in items:
            self.session.add(item)
            entities.append(item)
        self.session.flush()
        self.session.commit()
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
