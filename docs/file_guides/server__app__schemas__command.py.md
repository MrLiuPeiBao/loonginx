# 文件讲解：`server/app/schemas/command.py`

## 1. 文件定位
- **角色**：业务/基础模块
- **所在层级**：`server/app/schemas`
- **是否建议新人优先阅读**：是
- **模块文档摘要**：命令日志与请求模型。

## 2. 对外依赖与协作关系
- **导入依赖（节选）**：
  - `from __future__ import annotations`
  - `from datetime import datetime`
  - `from typing import List, Optional, Union`
  - `from sqlmodel import SQLModel`
  - `from app.db.models import CommandDirection, CommandStatus`

## 3. 核心模块与实现原理
- **类设计**：
  - `CommandLogRead`（继承：SQLModel）
    - 作用：命令日志读取模型。
    - 方法数量：0
  - `CommandRequest`（继承：SQLModel）
    - 作用：命令发送请求。
    - 方法数量：0
  - `CommandRequestStatusRead`（继承：SQLModel）
    - 作用：命令请求状态读取模型。
    - 方法数量：0

## 4. 新人阅读建议（针对本文件）
- 建议先看公开函数/类，再沿“关键调用链”逐跳进入下层模块。
- 对 I/O 代码（MQTT、串口、DB）重点关注异常路径与重试策略。
- 可结合对应测试文件验证你对边界条件的理解。
