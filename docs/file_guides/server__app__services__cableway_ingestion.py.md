# 文件讲解：`server/app/services/cableway_ingestion.py`

## 1. 文件定位
- **角色**：业务/基础模块
- **所在层级**：`server/app/services`
- **是否建议新人优先阅读**：是
- **模块文档摘要**：索道 PLC 状态 MQTT 入库逻辑。

## 2. 对外依赖与协作关系
- **导入依赖（节选）**：
  - `from __future__ import annotations`
  - `json`
  - `from datetime import datetime`
  - `from typing import Any, Optional`
  - `from app.db.models import CablewayStatus`
  - `from app.db.session import session_scope`
  - `from app.mqtt import MQTTManager, MQTTMessageContext`
  - `from app.services.alarm_publisher import build_alarm_event, publish_alarm_event`
  - `from app.services.cableway_alerts import create_cableway_alarm_events, extract_status_payload`
  - `from app.services.data_service import DataService`
  - `from app.services.cableway_cache import set_latest_cableway_status`
  - `from app.services.plc_logging import get_plc_logger`

## 3. 核心模块与实现原理
- **关键常量**：`DEFAULT_DEVICE_ID, DEFAULT_LOCATION`
- **函数设计**：
  - `_to_local_naive(value: datetime)`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：replace, value.astimezone
  - `_parse_datetime(value: Any)`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：_to_local_naive, datetime.fromisoformat, datetime.fromtimestamp, datetime.now, float, isinstance, text.replace, value.strip
  - `_to_str(value: Any, default: str)`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：str, strip
  - `_resolve_device_id(base: dict, default_device_id: str)`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：_to_str, base.get
  - `_resolve_location(base: dict, default_location: str)`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：_to_str, base.get
  - `_decode_payload(context: MQTTMessageContext)`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：context.payload.decode, json.loads, len, plc_logger.debug, plc_logger.error
  - `handle_cableway_status_payload(context: MQTTMessageContext, mqtt_manager: Optional[MQTTManager] = None)`
    - 功能：处理 `cableway/status` 主题消息并入库。
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：CablewayStatus, DataService, _decode_payload, _parse_datetime, _resolve_device_id, _resolve_location, build_alarm_event, create_cableway_alarm_events

## 4. 新人阅读建议（针对本文件）
- 建议先看公开函数/类，再沿“关键调用链”逐跳进入下层模块。
- 对 I/O 代码（MQTT、串口、DB）重点关注异常路径与重试策略。
- 可结合对应测试文件验证你对边界条件的理解。
