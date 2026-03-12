# 文件讲解：`server/app/services/data_service.py`

## 1. 文件定位
- **角色**：业务/基础模块
- **所在层级**：`server/app/services`
- **是否建议新人优先阅读**：是
- **模块文档摘要**：High level data access helpers built on top of SQLModel.

## 2. 对外依赖与协作关系
- **导入依赖（节选）**：
  - `from __future__ import annotations`
  - `from datetime import datetime, timedelta`
  - `from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple`
  - `from sqlmodel import Session, select`
  - `from sqlalchemy import delete, text`
  - `from app.db.models import AlarmRecord, AlarmType, AudioData, BMSData, CablewayStatus, CommandDirection, CommandLog, CommandRequestState, CommandStatus, ImageData, MetalAnomaly, RFIDData, SensorConfig, SensorData`
  - `from app.core.config import get_settings`
  - `from app.services.media_storage import store_audio_bytes, store_image_base64`
  - `from .alarm_publisher import build_alarm_event`
  - `from .alarm_cache import set_latest_alarm`

## 3. 核心模块与实现原理
- **关键常量**：`RETENTION_TABLES, SENSOR_VALUE_FIELDS`
- **类设计**：
  - `DataService`（继承：无）
    - 作用：Wrap SQLModel operations with domain specific helpers.
    - 方法数量：37
    - `__init__(self, session: Session)`
    - `consume_alarm_events(self)`
      - 职责：取出并清空最近一次写入过程中生成的告警事件。
      - 关键调用链：list, self._created_alarm_events.clear
    - `create_sensor_data(self, items: Iterable[SensorData])`
      - 职责：Persist sensor readings, merging identical timestamps per device/location.
      - 关键调用链：evaluation_plan.items, evaluation_plan.setdefault, id, max, seen_entities.add, self._created_alarm_events.clear
    - `list_sensor_data(self, *, start: Optional[datetime] = None, end: Optional[datetime] = None, device_id: Optional[str] = None, location: Optional[str] = None, limit: Optional[int] = None, offset: Optional[int] = None)`
      - 关键调用链：SensorData.timestamp.desc, list, order_by, select, self.session.exec, statement.limit
    - `create_bms_data(self, items: Iterable[BMSData])`
      - 关键调用链：self._persist_entities
    - `list_bms_data(self, *, start: Optional[datetime] = None, end: Optional[datetime] = None, device_id: Optional[str] = None, limit: Optional[int] = None, offset: Optional[int] = None)`
      - 关键调用链：BMSData.timestamp.desc, list, order_by, select, self.session.exec, statement.limit
    - `create_rfid_data(self, items: Iterable[RFIDData])`
      - 关键调用链：self._persist_entities
    - `list_rfid_data(self, *, start: Optional[datetime] = None, end: Optional[datetime] = None, device_id: Optional[str] = None, limit: Optional[int] = None, offset: Optional[int] = None)`
      - 关键调用链：RFIDData.timestamp.desc, list, order_by, select, self.session.exec, statement.limit
    - ... 其余 29 个方法建议在 IDE 中按调用层级继续追踪
- **函数设计**：
  - `_resolve_retention_tables(tables: Optional[Iterable[str]])`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：RETENTION_TABLES.get, RETENTION_TABLES.values, list, resolved.append, str
  - `_estimate_db_size_gb()`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：conn.execute, engine.connect, float, get_settings, scalar, text

## 4. 新人阅读建议（针对本文件）
- 建议先看公开函数/类，再沿“关键调用链”逐跳进入下层模块。
- 对 I/O 代码（MQTT、串口、DB）重点关注异常路径与重试策略。
- 可结合对应测试文件验证你对边界条件的理解。
