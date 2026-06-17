# 文件讲解：`client/tests/test_mqtt_client_offline.py`

## 1. 文件定位
- **角色**：测试文件
- **所在层级**：`client/tests`
- **是否建议新人优先阅读**：作为回归验证参考

## 2. 对外依赖与协作关系
- **导入依赖（节选）**：
  - `from __future__ import annotations`
  - `sys`
  - `types`

## 3. 核心模块与实现原理
- **函数设计**：
  - `_install_dummy_paho()`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：DummyResult, types.ModuleType, types.SimpleNamespace
  - `test_offline_latest_queue(monkeypatch)`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：MQTTClient, _install_dummy_paho, client._resolve_qos, client.publish, getattr

## 4. 新人阅读建议（针对本文件）
- 先看 fixture 与断言目标，理解它覆盖的业务规则。
- 将测试名映射到被测模块，作为阅读业务代码的导航。
