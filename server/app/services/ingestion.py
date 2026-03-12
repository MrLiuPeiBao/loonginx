"""MQTT 消息入库处理逻辑。"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.db.models import BMSData, RFIDData, SensorData
from app.core.config import get_settings
from app.db.session import session_scope
from app.mqtt import MQTTManager, MQTTMessageContext
from app.services.alarm_publisher import build_alarm_event, publish_alarm_event
from app.services.bms_cache import set_latest_bms
from app.services.bms_alerts import maybe_create_bms_low_voltage_alarm
from app.services.data_service import DataService, SENSOR_VALUE_FIELDS
from app.services.rfid_cache import set_latest_rfid
from app.services.sensor_cache import set_latest_sensor

logger = logging.getLogger(__name__)

BMS_FIELDS: Tuple[str, ...] = ('voltage', 'soc', 'status', 'capacity', 'power', 'current')
DEFAULT_DEVICE_ID = 'gateway'
DEFAULT_LOCATION = 'unknown'
SENSOR_GROUP_WINDOW_SECONDS = 2.0


def _publish_ingestion_failure(
    *,
    mqtt_manager: Optional[MQTTManager],
    context: MQTTMessageContext,
    device_id: str,
    location: str,
    payload_type: str,
    error: Exception,
) -> None:
    event = build_alarm_event(
        source='ingestion_failed',
        timestamp=datetime.now(),
        device_id=device_id,
        location=location,
        payload={
            'topic': context.topic,
            'payload_type': payload_type,
            'error': str(error),
        },
    )
    publish_alarm_event(mqtt_manager, event)


def _to_local_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone().replace(tzinfo=None)


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return _to_local_naive(value)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value))
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            return _to_local_naive(parsed)
        except ValueError:
            pass
    return datetime.now()


def _to_str(value: Any, default: str = 'unknown') -> str:
    if value is None:
        return default
    return str(value)


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _decode_payload(context: MQTTMessageContext) -> Optional[Any]:
    try:
        text = context.payload.decode('utf-8')
    except UnicodeDecodeError:
        logger.error('MQTT payload decode failed for topic %s', context.topic)
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.error('MQTT payload is not valid JSON on topic %s', context.topic)
        return None


def _resolve_device_id(base: Dict[str, Any], default_device_id: str) -> str:
    for key in ('device_id', 'gateway_id', 'gateway', 'node_id', 'client_id'):
        value = base.get(key)
        if value:
            return _to_str(value)
    return default_device_id


def _resolve_location(base: Dict[str, Any], default_location: str) -> str:
    return _to_str(base.get('location') or base.get('site') or base.get('area') or default_location)


def _build_sensor_record(
    base: Dict[str, Any],
    sensor_map: Dict[str, Any],
    *,
    default_device_id: str,
    default_location: str,
) -> Optional[SensorData]:
    timestamp = _parse_datetime(base.get('timestamp'))
    device_id = _resolve_device_id(base, default_device_id)
    location = _resolve_location(base, default_location)

    values = {
        field: _safe_float(sensor_map.get(field))
        for field in SENSOR_VALUE_FIELDS
    }

    if any(values[field] is None for field in SENSOR_VALUE_FIELDS):
        logger.debug(
            'Sensor dataset discarded (missing fields) for device=%s timestamp=%s',
            device_id,
            timestamp.isoformat(),
        )
        return None

    return SensorData(
        timestamp=timestamp,
        device_id=device_id,
        location=location,
        **values,
    )


def _extract_sensor_map(message: Dict[str, Any]) -> Dict[str, Any]:
    sensor_map: Dict[str, Any] = {}
    if isinstance(message.get('sensors'), dict):
        sensor_map = message['sensors']
    elif isinstance(message.get('readings'), dict):
        sensor_map = message['readings']
    elif isinstance(message.get('readings'), list):
        for item in message['readings']:
            if isinstance(item, dict):
                sensor_type = item.get('type') or item.get('sensor')
                if sensor_type:
                    sensor_map[sensor_type] = item.get('value')
    else:
        for field in SENSOR_VALUE_FIELDS:
            if field in message:
                sensor_map[field] = message[field]
    return sensor_map


def _parse_sensor_message(
    message: Any,
    *,
    default_device_id: str = DEFAULT_DEVICE_ID,
    default_location: str = DEFAULT_LOCATION,
) -> List[SensorData]:
    records: List[SensorData] = []
    now = datetime.now()

    if isinstance(message, dict):
        sensor_map = _extract_sensor_map(message)
        if sensor_map:
            record = _build_sensor_record(
                message,
                sensor_map,
                default_device_id=_resolve_device_id(message, default_device_id),
                default_location=_resolve_location(message, default_location),
            )
            if record:
                records.append(record)
        return records

    if isinstance(message, list):
        grouped: Dict[Tuple[str, str, int], Dict[str, Any]] = {}
        group_meta: Dict[Tuple[str, str, int], Dict[str, Any]] = {}
        anchors: Dict[Tuple[str, str], datetime] = {}
        for item in message:
            if not isinstance(item, dict):
                continue
            sensor_type = item.get('sensor_type') or item.get('sensor') or item.get('type')
            if sensor_type not in SENSOR_VALUE_FIELDS:
                continue
            timestamp = _parse_datetime(item.get('timestamp') or now)
            device_id = _resolve_device_id(item, default_device_id)
            location = _resolve_location(item, default_location)
            anchor_key = (device_id, location)
            anchor = anchors.get(anchor_key)
            if anchor is None or timestamp < anchor:
                anchor = timestamp
                anchors[anchor_key] = anchor
            delta = max(0.0, (timestamp - anchor).total_seconds())
            bucket = int(delta // SENSOR_GROUP_WINDOW_SECONDS)
            key = (device_id, location, bucket)
            grouped.setdefault(key, {})[sensor_type] = item.get('value')
            meta = group_meta.setdefault(key, {'timestamp': timestamp})
            if timestamp > meta['timestamp']:
                meta['timestamp'] = timestamp

        for (device_id, location, bucket), sensor_map in grouped.items():
            base = {
                'device_id': device_id,
                'timestamp': group_meta[(device_id, location, bucket)]['timestamp'],
                'location': location,
            }
            record = _build_sensor_record(
                base,
                sensor_map,
                default_device_id=device_id,
                default_location=location,
            )
            if record:
                records.append(record)
    return records


def _parse_bms_message(message: Any) -> Optional[BMSData]:
    if not isinstance(message, dict):
        return None
    timestamp = _parse_datetime(message.get('timestamp'))
    device_id = _to_str(message.get('device_id') or message.get('gateway_id'))
    location = _to_str(message.get('location'))

    data: Dict[str, Any] = {}
    if isinstance(message.get('data'), dict):
        data = message['data']
    else:
        for field in BMS_FIELDS:
            if field in message:
                data[field] = message[field]

    if not data:
        return None

    values: Dict[str, Any] = {}
    for field in BMS_FIELDS:
        if field == 'status':
            try:
                values[field] = int(data.get(field))
            except (TypeError, ValueError):
                values[field] = None
        else:
            values[field] = _safe_float(data.get(field))

    raw_cells = data.get('cell_voltages') or message.get('cell_voltages')
    parsed_cells: Optional[List[Optional[float]]] = None
    if isinstance(raw_cells, list):
        parsed_cells = []
        for item in raw_cells:
            parsed_cells.append(_safe_float(item))

    return BMSData(
        timestamp=timestamp,
        device_id=device_id,
        location=location,
        cell_voltages=parsed_cells,
        **values,
    )


def _parse_rfid_message(message: Any) -> Optional[RFIDData]:
    if not isinstance(message, dict):
        return None
    card_id = message.get('card_id') or message.get('uid')
    raw_data = message.get('raw_data') or message.get('hex') or card_id
    if not card_id and not raw_data:
        return None

    timestamp = _parse_datetime(message.get('timestamp'))
    device_id = _to_str(message.get('device_id') or message.get('reader_id') or 'rfid')
    location = _to_str(message.get('location'))
    length = len(str(card_id)) if card_id else None

    return RFIDData(
        timestamp=timestamp,
        device_id=device_id,
        card_id=str(card_id or raw_data),
        raw_data=str(raw_data),
        length=length,
        location=location,
    )


def handle_sensor_payload(
    context: MQTTMessageContext,
    mqtt_manager: Optional[MQTTManager] = None,
) -> None:
    """Process sensor MQTT payloads."""
    message = _decode_payload(context)
    if message is None:
        return

    settings = get_settings()
    default_device = _to_str(context.topic)
    default_location = DEFAULT_LOCATION
    base_obj = None
    if isinstance(message, dict):
        base_obj = message
    elif isinstance(message, list):
        base_obj = next((item for item in message if isinstance(item, dict)), None)
    if base_obj:
        payload_device = _resolve_device_id(base_obj, default_device)
        if settings.prefer_payload_device_id and payload_device:
            default_device = payload_device
        default_location = _resolve_location(base_obj, DEFAULT_LOCATION)

    events: List[Dict[str, Any]] = []
    try:
        with session_scope() as session:
            service = DataService(session)
            latest_rfid = service.get_latest_rfid_card()
            location_override = latest_rfid or default_location
            records = _parse_sensor_message(
                message,
                default_device_id=default_device,
                default_location=location_override,
            )
            if not records:
                logger.debug('No sensor records parsed from payload on %s', context.topic)
                return
            for record in records:
                record.device_id = default_device
                if latest_rfid:
                    record.location = latest_rfid
            stored = service.create_sensor_data(records)
            if stored:
                latest = max(stored, key=lambda item: item.timestamp)
                set_latest_sensor(latest)
            events = service.consume_alarm_events()
    except Exception as exc:  # pragma: no cover - database/network
        logger.exception('Failed to persist sensor data from MQTT')
        _publish_ingestion_failure(
            mqtt_manager=mqtt_manager,
            context=context,
            device_id=default_device,
            location=default_location,
            payload_type='sensor_data',
            error=exc,
        )
        return

    for event in events:
        publish_alarm_event(mqtt_manager, event)


def handle_bms_payload(
    context: MQTTMessageContext,
    mqtt_manager: Optional[MQTTManager] = None,
) -> None:
    """处理 BMS 数据主题。"""
    message = _decode_payload(context)
    if message is None:
        return
    alarm_event: Optional[Dict[str, Any]] = None
    settings = get_settings()
    payload_device = _resolve_device_id(message, _to_str(context.topic))
    fallback_device = payload_device if settings.prefer_payload_device_id else _to_str(context.topic)
    fallback_location = _resolve_location(message, DEFAULT_LOCATION)
    try:
        with session_scope() as session:
            service = DataService(session)
            latest_rfid = service.get_latest_rfid_card()
            entity = _parse_bms_message(message)
            if entity is None:
                logger.debug('No BMS record parsed from payload on %s', context.topic)
                return
            entity.device_id = payload_device if settings.prefer_payload_device_id else _to_str(context.topic)
            if latest_rfid:
                entity.location = latest_rfid
            stored = service.create_bms_data([entity])
            if stored:
                set_latest_bms(stored[-1])
            alarm_event = maybe_create_bms_low_voltage_alarm(service, entity)
    except Exception as exc:  # pragma: no cover
        logger.exception('Failed to persist BMS data from MQTT')
        _publish_ingestion_failure(
            mqtt_manager=mqtt_manager,
            context=context,
            device_id=fallback_device,
            location=fallback_location,
            payload_type='bms_data',
            error=exc,
        )
        return

    if alarm_event:
        publish_alarm_event(mqtt_manager, alarm_event)


def handle_rfid_payload(context: MQTTMessageContext) -> None:
    """处理 RFID 数据主题。"""
    message = _decode_payload(context)
    if message is None:
        return
    settings = get_settings()
    payload_device = _resolve_device_id(message, _to_str(context.topic))
    fallback_device = payload_device if settings.prefer_payload_device_id else _to_str(context.topic)
    fallback_location = _resolve_location(message, DEFAULT_LOCATION)
    try:
        with session_scope() as session:
            service = DataService(session)
            entity = _parse_rfid_message(message)
            if entity is None:
                logger.debug('No RFID record parsed from payload on %s', context.topic)
                return
            entity.device_id = payload_device if settings.prefer_payload_device_id else _to_str(context.topic)
            stored = service.create_rfid_data([entity])
            if stored:
                latest = max(stored, key=lambda item: item.timestamp)
                set_latest_rfid(latest)
    except Exception as exc:  # pragma: no cover
        logger.exception('Failed to persist RFID data from MQTT')
        _publish_ingestion_failure(
            mqtt_manager=None,
            context=context,
            device_id=fallback_device,
            location=fallback_location,
            payload_type='rfid_data',
            error=exc,
        )
