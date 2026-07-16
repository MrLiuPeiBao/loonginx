"""统一的 MQTT 告警广播发布器。

事件会发布到 `MQTT_TOPICS['alarm_broadcast']`，用于前端/GUI 统一订阅告警通知。

Schema v1（示例）::

    {
      "schema": 1,
      "event": "alarm",
      "source": "sensor_threshold",
      "timestamp": "2025-12-16T10:00:00.000000",
      "device_id": "sensors/data/gateway01",
      "location": "CARD123",
      "payload": {...}
    }
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional, TYPE_CHECKING

from app.core.constants import MQTT_TOPICS

if TYPE_CHECKING:  # pragma: no cover
    from app.mqtt import MQTTManager

logger = logging.getLogger(__name__)

ALARM_EVENT_SCHEMA_VERSION = 1


def build_alarm_event(
    *,
    source: str,
    timestamp: datetime,
    device_id: Optional[str],
    location: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """构建统一告警事件 payload。

    Args:
        source (str): 告警来源/类型（如 sensor_threshold/audio_threshold/yolo/api_alarm）。
        timestamp (datetime): 告警发生时间。
        device_id (Optional[str]): 设备标识（推荐填充为 MQTT topic 全串）。
        location (str): 位置标识（推荐填充为最新 RFID card_id）。
        payload (Dict[str, Any]): 业务载荷（具体字段由 source 决定）。

    Returns:
        Dict[str, Any]: 统一的告警事件字典。
    """
    safe_location = location or ''
    return {
        'schema': ALARM_EVENT_SCHEMA_VERSION,
        'event': 'alarm',
        'source': source,
        'timestamp': timestamp.isoformat(),
        'device_id': device_id,
        'location': safe_location,
        'payload': payload,
    }


def publish_alarm_event(
    mqtt_manager: Optional["MQTTManager"],
    event: Dict[str, Any],
    *,
    qos: int = 1,
    retain: bool = False,
) -> bool:
    """发布告警事件到 MQTT 广播主题。

    Args:
        mqtt_manager (Optional[MQTTManager]): MQTT 管理器；为空时直接跳过发布。
        event (Dict[str, Any]): `build_alarm_event()` 生成的事件字典。
        qos (int): MQTT QoS，默认 1。
        retain (bool): 是否 retain，默认 False。

    Returns:
        bool: 是否发布成功（未连接或 mqtt_manager 为空返回 False）。
    """
    if mqtt_manager is None:
        logger.debug('Alarm publish skipped: mqtt_manager is None')
        return False

    try:
        payload_bytes = json.dumps(event, ensure_ascii=False).encode('utf-8')
    except (TypeError, ValueError):
        logger.exception('Alarm event JSON encode failed: %s', event)
        return False

    topic = MQTT_TOPICS['alarm_broadcast']
    ok = mqtt_manager.publish(topic, payload=payload_bytes, qos=qos, retain=retain)
    if ok:
        logger.info('Alarm event published to %s source=%s', topic, event.get('source'))
    else:
        logger.warning('Alarm event publish failed to %s source=%s', topic, event.get('source'))
    return ok


# backwards compat: sync publish_alarm_event is the single implementation
async def publish_alarm_event_async(
    mqtt_manager: Optional["MQTTManager"],
    event: Dict[str, Any],
    *,
    qos: int = 1,
    retain: bool = False,
) -> bool:
    return await asyncio.to_thread(publish_alarm_event, mqtt_manager, event, qos=qos, retain=retain)
