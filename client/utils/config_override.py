"""运行期配置覆盖加载与合并。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional


DEFAULT_OVERRIDE_PATH = Path(__file__).resolve().parents[1] / 'config_override.json'
CONFIG_VERSION_KEY = '__config_version'
CONFIG_UPDATED_AT_KEY = '__config_updated_at'

HOT_UPDATE_KEYS = {
    'SENSOR_POLL_DELAY',
    'SENSOR_RETRY_DELAY',
    'MAX_SENSOR_ATTEMPTS',
    'LOOP_IDLE_DELAY',
    'BMS_POLL_INTERVAL',
    'MQTT_DEGRADE_ENTER_SECONDS',
    'MQTT_DEGRADE_EXIT_SECONDS',
    'MQTT_DEGRADE_FACTOR',
    'MQTT_PUBLISH_QOS',
    'MQTT_OFFLINE_QUEUE_ENABLED',
    'MQTT_OFFLINE_QUEUE_MAX_ITEMS',
    'MQTT_OFFLINE_QUEUE_MAX_BYTES',
    'MQTT_OFFLINE_POLICY',
    'MQTT_TOPIC_QOS_MAP',
}


def load_overrides(path: Optional[Path] = None) -> Dict[str, object]:
    target = path or DEFAULT_OVERRIDE_PATH
    if not target.exists():
        return {}
    try:
        raw = target.read_text(encoding='utf-8')
    except OSError:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def save_overrides(overrides: Dict[str, object], path: Optional[Path] = None) -> None:
    target = path or DEFAULT_OVERRIDE_PATH
    target.write_text(
        json.dumps(overrides or {}, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )


def get_config_version(overrides: Optional[Dict[str, object]] = None) -> int:
    data = overrides or load_overrides()
    value = data.get(CONFIG_VERSION_KEY, 0)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def get_config_updated_at(overrides: Optional[Dict[str, object]] = None) -> Optional[float]:
    data = overrides or load_overrides()
    value = data.get(CONFIG_UPDATED_AT_KEY)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def merge_overrides(base: Dict[str, object], overrides: Optional[Dict[str, object]]) -> Dict[str, object]:
    merged = dict(base or {})
    merged.update(overrides or {})
    return merged


def split_hot_and_restart(overrides: Optional[Dict[str, object]]) -> tuple[Dict[str, object], Dict[str, object]]:
    hot: Dict[str, object] = {}
    pending: Dict[str, object] = {}
    for key, value in (overrides or {}).items():
        if key in HOT_UPDATE_KEYS:
            hot[key] = value
        else:
            pending[key] = value
    return hot, pending
