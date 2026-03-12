# 文件讲解：`server/app/services/config_sync.py`

## 1. 文件定位
- **角色**：业务/基础模块
- **所在层级**：`server/app/services`
- **是否建议新人优先阅读**：是
- **模块文档摘要**：配置下发与回执处理。

## 2. 对外依赖与协作关系
- **导入依赖（节选）**：
  - `from __future__ import annotations`
  - `json`
  - `logging`
  - `from datetime import datetime`
  - `from typing import TYPE_CHECKING, Dict`
  - `from app.core.constants import MQTT_TOPICS`
  - `from app.db.models import CommandDirection`
  - `from app.services.data_service import DataService`

## 3. 核心模块与实现原理
- **函数设计**：
  - `build_config_update_payload(payload: Dict[str, object], version: int)`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：datetime.now, int, isoformat
  - `build_device_hello(*, version: int)`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：int
  - `publish_config_update(mqtt_manager: 'MQTTManager', overrides: Dict[str, object], *, version: int)`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：build_config_update_payload, encode, json.dumps, mqtt_manager.publish
  - `handle_config_ack(context: 'MQTTMessageContext')`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：DataService, add_command_log, context.json, datetime.now, json.dumps, logger.exception, logger.info, payload.get
  - `handle_device_hello(context: 'MQTTMessageContext', mqtt_manager: 'MQTTManager')`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：context.json, get_runtime_config_row, int, load_runtime_overrides, logger.exception, logger.warning, payload.get, publish_config_update

## 4. 新人阅读建议（针对本文件）
- 建议先看公开函数/类，再沿“关键调用链”逐跳进入下层模块。
- 对 I/O 代码（MQTT、串口、DB）重点关注异常路径与重试策略。
- 可结合对应测试文件验证你对边界条件的理解。
