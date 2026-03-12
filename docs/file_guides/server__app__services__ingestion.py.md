# 文件讲解：`server/app/services/ingestion.py`

## 1. 文件定位
- **角色**：业务/基础模块
- **所在层级**：`server/app/services`
- **是否建议新人优先阅读**：是
- **模块文档摘要**：MQTT 消息入库处理逻辑。

## 2. 对外依赖与协作关系
- **导入依赖（节选）**：
  - `from __future__ import annotations`
  - `json`
  - `logging`
  - `from datetime import datetime`
  - `from typing import Any, Dict, List, Optional, Tuple`
  - `from app.db.models import BMSData, RFIDData, SensorData`
  - `from app.core.config import get_settings`
  - `from app.db.session import session_scope`
  - `from app.mqtt import MQTTManager, MQTTMessageContext`
  - `from app.services.alarm_publisher import build_alarm_event, publish_alarm_event`
  - `from app.services.bms_cache import set_latest_bms`
  - `from app.services.bms_alerts import maybe_create_bms_low_voltage_alarm`
  - `from app.services.data_service import DataService, SENSOR_VALUE_FIELDS`
  - `from app.services.rfid_cache import set_latest_rfid`
  - `from app.services.sensor_cache import set_latest_sensor`

## 3. 核心模块与实现原理
- **关键常量**：`BMS_FIELDS, DEFAULT_DEVICE_ID, DEFAULT_LOCATION, SENSOR_GROUP_WINDOW_SECONDS`
- **函数设计**：
  - `_publish_ingestion_failure(*, mqtt_manager: Optional[MQTTManager], context: MQTTMessageContext, device_id: str, location: str, payload_type: str, error: Exception)`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：build_alarm_event, datetime.now, publish_alarm_event, str
  - `_to_local_naive(value: datetime)`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：replace, value.astimezone
  - `_parse_datetime(value: Any)`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：_to_local_naive, datetime.fromisoformat, datetime.fromtimestamp, datetime.now, float, isinstance, value.replace
  - `_to_str(value: Any, default: str = 'unknown')`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：str
  - `_safe_float(value: Any)`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：float, isinstance
  - `_decode_payload(context: MQTTMessageContext)`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：context.payload.decode, json.loads, logger.error
  - `_resolve_device_id(base: Dict[str, Any], default_device_id: str)`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：_to_str, base.get
  - `_resolve_location(base: Dict[str, Any], default_location: str)`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：_to_str, base.get
  - `_build_sensor_record(base: Dict[str, Any], sensor_map: Dict[str, Any], *, default_device_id: str, default_location: str)`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：SensorData, _parse_datetime, _resolve_device_id, _resolve_location, _safe_float, any, base.get, logger.debug
  - `_extract_sensor_map(message: Dict[str, Any])`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：isinstance, item.get, message.get
  - `_parse_sensor_message(message: Any, *, default_device_id: str = DEFAULT_DEVICE_ID, default_location: str = DEFAULT_LOCATION)`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：_build_sensor_record, _extract_sensor_map, _parse_datetime, _resolve_device_id, _resolve_location, anchors.get, datetime.now, group_meta.setdefault
  - `_parse_bms_message(message: Any)`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：BMSData, _parse_datetime, _safe_float, _to_str, data.get, int, isinstance, message.get
  - `_parse_rfid_message(message: Any)`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：RFIDData, _parse_datetime, _to_str, isinstance, len, message.get, str
  - `handle_sensor_payload(context: MQTTMessageContext, mqtt_manager: Optional[MQTTManager] = None)`
    - 功能：Process sensor MQTT payloads.
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：DataService, _decode_payload, _parse_sensor_message, _publish_ingestion_failure, _resolve_device_id, _resolve_location, _to_str, get_settings
  - `handle_bms_payload(context: MQTTMessageContext, mqtt_manager: Optional[MQTTManager] = None)`
    - 功能：处理 BMS 数据主题。
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：DataService, _decode_payload, _parse_bms_message, _publish_ingestion_failure, _resolve_device_id, _resolve_location, _to_str, get_settings
  - `handle_rfid_payload(context: MQTTMessageContext)`
    - 功能：处理 RFID 数据主题。
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：DataService, _decode_payload, _parse_rfid_message, _publish_ingestion_failure, _resolve_device_id, _resolve_location, _to_str, get_settings

## 4. 新人阅读建议（针对本文件）
- 建议先看公开函数/类，再沿“关键调用链”逐跳进入下层模块。
- 对 I/O 代码（MQTT、串口、DB）重点关注异常路径与重试策略。
- 可结合对应测试文件验证你对边界条件的理解。
