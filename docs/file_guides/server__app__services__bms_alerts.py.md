# 文件讲解：`server/app/services/bms_alerts.py`

## 1. 文件定位
- **角色**：业务/基础模块
- **所在层级**：`server/app/services`
- **是否建议新人优先阅读**：是
- **模块文档摘要**：BMS 侧的告警逻辑（如单体低压）。

## 2. 对外依赖与协作关系
- **导入依赖（节选）**：
  - `from __future__ import annotations`
  - `logging`
  - `from datetime import datetime, timedelta`
  - `from typing import Any, Dict, Optional`
  - `from sqlmodel import select`
  - `from app.db.models import AlarmRecord, AlarmType, BMSData`
  - `from .alarm_publisher import build_alarm_event`
  - `from .data_service import DataService`

## 3. 核心模块与实现原理
- **关键常量**：`BMS_ALARM_DEBOUNCE_SECONDS, BMS_CELL_VOLTAGE_THRESHOLD, BMS_CHARGE_COMMAND_HEX`
- **函数设计**：
  - `maybe_create_bms_low_voltage_alarm(data_service: DataService, bms_data: BMSData, *, threshold: float = BMS_CELL_VOLTAGE_THRESHOLD, debounce_seconds: int = BMS_ALARM_DEBOUNCE_SECONDS)`
    - 功能：检测 BMS 单体低压并落库 + 生成 MQTT 告警事件（如需）。
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：AlarmRecord, build_alarm_event, data_service.create_alarm_record, data_service.session.exec, datetime.now, first, float, int

## 4. 新人阅读建议（针对本文件）
- 建议先看公开函数/类，再沿“关键调用链”逐跳进入下层模块。
- 对 I/O 代码（MQTT、串口、DB）重点关注异常路径与重试策略。
- 可结合对应测试文件验证你对边界条件的理解。
