# 文件讲解：`server/app/services/cableway_specs.py`

## 1. 文件定位
- **角色**：业务/基础模块
- **所在层级**：`server/app/services`
- **是否建议新人优先阅读**：是
- **模块文档摘要**：索道 PLC（西门子 S7-200 SMART）点表与校验逻辑。

## 2. 对外依赖与协作关系
- **导入依赖（节选）**：
  - `from __future__ import annotations`
  - `from dataclasses import dataclass`
  - `from typing import Dict, Optional, Tuple`

## 3. 核心模块与实现原理
- **关键常量**：`CONTROL_COMMANDS, FLOAT_PARAMS, HEARTBEAT_VALUES, VW_COMMAND, VW_ESTOP, VW_HEARTBEAT`
- **类设计**：
  - `FloatParamSpec`（继承：无）
    - 方法数量：0
- **函数设计**：
  - `validate_control_command_code(code: int)`
    - 功能：校验 VW2404 控制命令码。
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：ValueError, int
  - `validate_param_updates(params: Dict[str, float])`
    - 功能：校验参数更新请求（按点表范围限制）。
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：FLOAT_PARAMS.get, ValueError, float, isinstance, params.items, str

## 4. 新人阅读建议（针对本文件）
- 建议先看公开函数/类，再沿“关键调用链”逐跳进入下层模块。
- 对 I/O 代码（MQTT、串口、DB）重点关注异常路径与重试策略。
- 可结合对应测试文件验证你对边界条件的理解。
