# 文件讲解：`server/tests/test_data_service.py`

## 1. 文件定位
- **角色**：测试文件
- **所在层级**：`server/tests`
- **是否建议新人优先阅读**：作为回归验证参考
- **模块文档摘要**：DataService behavior tests.

## 2. 对外依赖与协作关系
- **导入依赖（节选）**：
  - `from __future__ import annotations`
  - `from datetime import datetime`
  - `pytest`
  - `from sqlmodel import select`
  - `from app.db.models import AlarmRecord, AlarmType, SensorConfig, SensorData`
  - `from app.services.data_service import DataService`

## 3. 核心模块与实现原理
- **函数设计**：
  - `_insert_temperature_config(session, max_threshold: float = 22.0)`
    - 功能：Insert a temperature threshold config to trigger alarms during tests.
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：SensorConfig, session.add, session.commit
  - `test_create_sensor_data_merges_and_triggers_alarm(session)`
    - 功能：Merging same-timestamp rows updates the record and emits one alarm.
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：DataService, SensorData, _insert_temperature_config, all, datetime, len, pytest.approx, select
  - `test_threshold_deduplication_by_timestamp(session)`
    - 功能：Duplicate alarms are suppressed for the same timestamp/location.
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：DataService, SensorData, _insert_temperature_config, all, datetime, len, one, pytest.approx

## 4. 新人阅读建议（针对本文件）
- 先看 fixture 与断言目标，理解它覆盖的业务规则。
- 将测试名映射到被测模块，作为阅读业务代码的导航。
