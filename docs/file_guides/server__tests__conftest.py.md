# 文件讲解：`server/tests/conftest.py`

## 1. 文件定位
- **角色**：测试文件
- **所在层级**：`server/tests`
- **是否建议新人优先阅读**：作为回归验证参考
- **模块文档摘要**：Test fixtures for isolated SQLModel sessions.

## 2. 对外依赖与协作关系
- **导入依赖（节选）**：
  - `from __future__ import annotations`
  - `from collections.abc import Iterator`
  - `pytest`
  - `from sqlalchemy.pool import StaticPool`
  - `from sqlmodel import SQLModel, Session, create_engine`
  - `from app.db import models`

## 3. 核心模块与实现原理
- **函数设计**：
  - `session()`
    - 功能：Provide an in-memory SQLite session for each test case.
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：SQLModel.metadata.create_all, Session, create_engine, pytest.fixture

## 4. 新人阅读建议（针对本文件）
- 先看 fixture 与断言目标，理解它覆盖的业务规则。
- 将测试名映射到被测模块，作为阅读业务代码的导航。
