# 文件讲解：`server/tests/test_ingestion_fallback.py`

## 1. 文件定位
- **角色**：测试文件
- **所在层级**：`server/tests`
- **是否建议新人优先阅读**：作为回归验证参考

## 2. 对外依赖与协作关系
- **导入依赖（节选）**：
  - `from __future__ import annotations`
  - `json`
  - `contextlib`
  - `from app.mqtt.client import MQTTMessageContext`
  - `from app.services import ingestion`

## 3. 核心模块与实现原理
- **函数设计**：
  - `_broken_session_scope()`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：RuntimeError, _ctx
  - `test_sensor_ingestion_emits_alarm_on_db_error(monkeypatch)`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：MQTTMessageContext, encode, events.append, get, ingestion.handle_sensor_payload, json.dumps, monkeypatch.setattr, object
  - `test_bms_ingestion_emits_alarm_on_db_error(monkeypatch)`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：MQTTMessageContext, encode, events.append, get, ingestion.handle_bms_payload, json.dumps, monkeypatch.setattr, object

## 4. 新人阅读建议（针对本文件）
- 先看 fixture 与断言目标，理解它覆盖的业务规则。
- 将测试名映射到被测模块，作为阅读业务代码的导航。
