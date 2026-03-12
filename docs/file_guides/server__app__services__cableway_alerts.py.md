# 文件讲解：`server/app/services/cableway_alerts.py`

## 1. 文件定位
- **角色**：业务/基础模块
- **所在层级**：`server/app/services`
- **是否建议新人优先阅读**：是
- **模块文档摘要**：索道 PLC 状态告警生成逻辑。

## 2. 对外依赖与协作关系
- **导入依赖（节选）**：
  - `from __future__ import annotations`
  - `from datetime import datetime`
  - `from typing import Any, Dict, List, Optional, Set, Tuple`
  - `from sqlmodel import select`
  - `from app.db.models import AlarmRecord, AlarmType, CablewayStatus`
  - `from app.services.alarm_publisher import build_alarm_event`
  - `from app.services.alarm_cache import set_latest_alarm`
  - `from app.services.data_service import DataService`

## 3. 核心模块与实现原理
- **关键常量**：`CABLEWAY_ALARM_SENSOR_KEY, CABLEWAY_ALARM_SOURCE, FAULT_NAME_BY_KEY, OUTPUT_ALARM_NAME_BY_KEY`
- **函数设计**：
  - `_to_bool_map(value: Any)`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：bool, isinstance, str, value.items
  - `_extract_active_keys(status_payload: Optional[dict])`
    - 功能：从 status 快照提取“应触发告警”的活跃键集合。
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：OUTPUT_ALARM_NAME_BY_KEY.items, _to_bool_map, active.add, faults.items, isinstance, outputs.get, set, status_payload.get
  - `_key_to_name(key: str)`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：FAULT_NAME_BY_KEY.get
  - `_recently_raised(data_service: DataService, *, location: str, sensor_name: str, timestamp: datetime, window_seconds: int = 10)`
    - 功能：对同一 location + sensor_name 做短窗口抑制，避免快速抖动造成重复告警。
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：AlarmRecord.timestamp.desc, data_service.session.exec, first, float, limit, order_by, select, total_seconds
  - `create_cableway_alarm_events(data_service: DataService, *, device_id: str, location: str, timestamp: datetime, current_status: dict, previous_status: Optional[dict])`
    - 功能：为新出现的故障/关键状态创建 DB 告警，并返回 MQTT 事件列表。
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：AlarmRecord, _extract_active_keys, _key_to_name, _recently_raised, _to_bool_map, build_alarm_event, created.append, current_status.get
  - `extract_status_payload(entity: Optional[CablewayStatus])`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：getattr, isinstance

## 4. 新人阅读建议（针对本文件）
- 建议先看公开函数/类，再沿“关键调用链”逐跳进入下层模块。
- 对 I/O 代码（MQTT、串口、DB）重点关注异常路径与重试策略。
- 可结合对应测试文件验证你对边界条件的理解。
