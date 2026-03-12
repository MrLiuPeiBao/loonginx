# 文件讲解：`server/tests/test_data_retention.py`

## 1. 文件定位
- **角色**：测试文件
- **所在层级**：`server/tests`
- **是否建议新人优先阅读**：作为回归验证参考

## 2. 对外依赖与协作关系
- **导入依赖（节选）**：
  - `from __future__ import annotations`
  - `from datetime import datetime, timedelta`
  - `from sqlmodel import select`
  - `from app.db.models import SensorData`
  - `from app.services.data_service import DataService`

## 3. 核心模块与实现原理
- **函数设计**：
  - `_create_sensor(ts: datetime)`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：SensorData
  - `test_prune_old_records_deletes_by_days(session)`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：DataService, _create_sensor, all, datetime.now, len, select, service.prune_old_records, session.add
  - `test_prune_by_size_removes_oldest_batch(session, monkeypatch)`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：DataService, _create_sensor, all, datetime.now, len, monkeypatch.setattr, select, service.prune_by_size

## 4. 新人阅读建议（针对本文件）
- 先看 fixture 与断言目标，理解它覆盖的业务规则。
- 将测试名映射到被测模块，作为阅读业务代码的导航。
