"""配置下发与回执处理。"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Dict, Optional

from app.core.constants import MQTT_TOPICS
from app.db.models import CommandDirection
from app.services.data_service import DataService

if TYPE_CHECKING:
    from app.mqtt import MQTTMessageContext
    from app.mqtt.client import MQTTManager
    from app.runtime.db_worker import DBWorker

logger = logging.getLogger(__name__)


def build_config_update_payload(payload: Dict[str, object], version: int) -> Dict[str, object]:
    return {
        'version': int(version),
        'payload': payload or {},
        'timestamp': datetime.now().isoformat(),
    }


def build_threshold_overrides(sensor_configs) -> Dict[str, object]:
    thresholds: Dict[str, Dict[str, float]] = {}
    for config in sensor_configs or []:
        sensor_type = getattr(config, 'type', None)
        if not sensor_type:
            continue
        thresholds[str(sensor_type)] = {
            'min': float(getattr(config, 'min_threshold', 0.0) or 0.0),
            'max': float(getattr(config, 'max_threshold', 0.0) or 0.0),
        }
    return {'LOCAL_SENSOR_THRESHOLDS': thresholds}


def build_device_hello(*, version: int) -> Dict[str, object]:
    return {
        'config_version': int(version),
    }


def publish_config_update(
    mqtt_manager: "MQTTManager",
    overrides: Dict[str, object],
    *,
    version: int,
) -> bool:
    payload = build_config_update_payload(overrides, version=version)
    data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    return mqtt_manager.publish(MQTT_TOPICS['config_update'], data)


def publish_threshold_config_update(
    mqtt_manager: "MQTTManager",
    sensor_configs,
    *,
    version: int = 0,
) -> bool:
    return publish_config_update(
        mqtt_manager,
        build_threshold_overrides(sensor_configs),
        version=version,
    )


def handle_config_ack(context: "MQTTMessageContext", *, db_worker: Optional["DBWorker"] = None) -> None:
    payload = context.json() or {}
    device_id = payload.get('device_id')
    status = payload.get('status') or payload.get('result')
    if status is None and 'success' in payload:
        status = 'ok' if payload.get('success') else 'error'
    pending = payload.get('pending_restart_keys') or []
    notes = f"config_ack status={status} pending={pending}"
    logger.info('Config ack from %s: %s', device_id or 'unknown', notes)
    if db_worker is None:
        logger.error('Skipping config ack persistence because db_worker is not configured')
        return
    try:
        def _persist(session):
            DataService(session).add_command_log(
                timestamp=datetime.now(),
                direction=CommandDirection.RESPONSE,
                payload=json.dumps(payload, ensure_ascii=False),
                notes=notes,
                device_id=device_id,
            )

        db_worker.call(_persist)
    except Exception:  # pragma: no cover - 依赖外部 DB
        logger.exception('Failed to persist config ack')


def handle_device_hello(
    context: "MQTTMessageContext",
    mqtt_manager: "MQTTManager",
    *,
    db_worker: Optional["DBWorker"] = None,
) -> None:
    payload = context.json() or {}
    try:
        device_version = int(payload.get('config_version') or 0)
    except (TypeError, ValueError):
        device_version = 0
    try:
        from app.services.runtime_config import get_runtime_config_row, load_runtime_overrides

        def _load(session):
            row = get_runtime_config_row(session)
            current_version = int(row.id) if row and row.id is not None else 0
            sensor_configs = DataService(session).list_sensor_configs()
            threshold_overrides = build_threshold_overrides(sensor_configs)
            if device_version == current_version:
                return current_version, threshold_overrides
            overrides = load_runtime_overrides(session)
            overrides.update(threshold_overrides)
            return current_version, overrides

        if db_worker is None:
            logger.error('Skipping device hello config sync because db_worker is not configured')
            return
        server_version, overrides = db_worker.call(_load)
        if overrides is None:
            return
    except Exception:
        logger.exception('Failed to resolve runtime config for device hello')
        return

    published = publish_config_update(
        mqtt_manager,
        overrides,
        version=server_version,
    )
    if not published:
        logger.warning('Failed to publish config update for device hello')
