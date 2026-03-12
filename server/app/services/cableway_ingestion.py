"""索道 PLC 状态 MQTT 入库逻辑。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from app.db.models import CablewayStatus
from app.db.session import session_scope
from app.mqtt import MQTTManager, MQTTMessageContext
from app.services.alarm_publisher import build_alarm_event, publish_alarm_event
from app.services.cableway_alerts import create_cableway_alarm_events, extract_status_payload
from app.services.data_service import DataService
from app.services.cableway_cache import set_latest_cableway_status
from app.services.plc_logging import get_plc_logger

plc_logger = get_plc_logger(__name__)
DEFAULT_DEVICE_ID = 'gateway'
DEFAULT_LOCATION = 'unknown'


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
        text = value.strip()
        if not text:
            return datetime.now()
        try:
            parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
            return _to_local_naive(parsed)
        except ValueError:
            return datetime.now()
    return datetime.now()


def _to_str(value: Any, default: str) -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _resolve_device_id(base: dict, default_device_id: str) -> str:
    for key in ('device_id', 'gateway_id', 'gateway', 'node_id', 'client_id'):
        value = base.get(key)
        if value:
            return _to_str(value, default_device_id)
    return default_device_id


def _resolve_location(base: dict, default_location: str) -> str:
    value = base.get('location') or base.get('site') or base.get('area')
    return _to_str(value, default_location)


def _decode_payload(context: MQTTMessageContext) -> Optional[Any]:
    plc_logger.debug(
        "Cableway status payload decode start topic=%s bytes=%s",
        context.topic,
        len(context.payload) if context.payload is not None else 0,
    )
    try:
        text = context.payload.decode('utf-8')
    except UnicodeDecodeError:
        plc_logger.error('MQTT payload decode failed for topic %s', context.topic)
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        plc_logger.error('MQTT payload is not valid JSON on topic %s', context.topic)
        return None


def handle_cableway_status_payload(
    context: MQTTMessageContext,
    mqtt_manager: Optional[MQTTManager] = None,
) -> None:
    """处理 `cableway/status` 主题消息并入库。"""
    message = _decode_payload(context)
    if not isinstance(message, dict):
        plc_logger.warning(
            "Cableway status payload ignored: not a dict topic=%s",
            context.topic,
        )
        return

    timestamp = _parse_datetime(message.get('timestamp'))
    device_id = _resolve_device_id(message, DEFAULT_DEVICE_ID)
    location = _resolve_location(message, DEFAULT_LOCATION)
    plc_host = message.get('plc_host') or message.get('host') or None
    if plc_host is not None:
        plc_host = str(plc_host)

    status_payload: dict
    if isinstance(message.get('status'), dict):
        status_payload = dict(message['status'])
    else:
        status_payload = dict(message)
    if plc_host is not None and 'plc_host' not in status_payload:
        status_payload['plc_host'] = plc_host
    if 'host' in message and 'host' not in status_payload:
        status_payload['host'] = message.get('host')

    events = []
    try:
        plc_logger.debug(
            "Cableway status parsed device_id=%s location=%s plc_host=%s timestamp=%s",
            device_id,
            location,
            plc_host,
            timestamp.isoformat(),
        )
        plc_logger.debug(
            "Cableway status payload keys=%s",
            sorted(status_payload.keys()),
        )
        with session_scope() as session:
            service = DataService(session)
            latest_rfid = service.get_latest_rfid_card()
            if not (message.get('location') or message.get('site') or message.get('area')) and latest_rfid:
                location = latest_rfid
                plc_logger.debug("Cableway status location override from RFID=%s", location)

            previous = service.get_latest_cableway_status(device_id=device_id)
            previous_payload = extract_status_payload(previous)

            entity = CablewayStatus(
                timestamp=timestamp,
                device_id=device_id,
                location=location,
                plc_host=plc_host,
                status=status_payload,
            )
            stored = service.create_cableway_status([entity])
            if stored:
                set_latest_cableway_status(stored[-1])
                plc_logger.info(
                    "Cableway status persisted device_id=%s id=%s",
                    device_id,
                    stored[-1].id,
                )

            events = create_cableway_alarm_events(
                service,
                device_id=device_id,
                location=location,
                timestamp=timestamp,
                current_status=status_payload,
                previous_status=previous_payload,
            )
    except Exception as exc:  # pragma: no cover - 依赖数据库
        plc_logger.exception('Failed to persist cableway status from MQTT')
        event = build_alarm_event(
            source='ingestion_failed',
            timestamp=datetime.now(),
            device_id=device_id,
            location=location,
            payload={
                'topic': context.topic,
                'payload_type': 'cableway_status',
                'error': str(exc),
            },
        )
        publish_alarm_event(mqtt_manager, event)
        return

    plc_logger.info(
        "Cableway status alarms generated count=%s",
        len(events),
    )
    for event in events:
        plc_logger.debug(
            "Cableway alarm publish source=%s level=%s",
            event.get('source'),
            event.get('level'),
        )
        publish_alarm_event(mqtt_manager, event)
