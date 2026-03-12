# 文件讲解：`server/app/db/models.py`

## 1. 文件定位
- **角色**：业务/基础模块
- **所在层级**：`server/app/db`
- **是否建议新人优先阅读**：是
- **模块文档摘要**：SQLModel ORM 定义。

## 2. 对外依赖与协作关系
- **导入依赖（节选）**：
  - `from __future__ import annotations`
  - `from datetime import datetime`
  - `from enum import Enum`
  - `from typing import List, Optional`
  - `from sqlmodel import Field, SQLModel`
  - `from sqlalchemy import Column, Index, LargeBinary, UniqueConstraint, JSON, Text`
  - `from sqlalchemy.dialects.mysql import LONGBLOB, LONGTEXT`

## 3. 核心模块与实现原理
- **关键常量**：`AUDIO_DATA_STORAGE_TYPE, CONFIG_JSON_STORAGE_TYPE`
- **类设计**：
  - `SensorTypeEnum`（继承：str, Enum）
    - 作用：传感器类型枚举。
    - 方法数量：0
  - `SensorConfig`（继承：SQLModel）
    - 作用：传感器阈值配置。
    - 方法数量：0
  - `RuntimeConfig`（继承：SQLModel）
    - 作用：运行期配置覆盖。
    - 方法数量：0
  - `SensorData`（继承：SQLModel）
    - 作用：环境传感器数据。
    - 方法数量：0
  - `AlarmType`（继承：str, Enum）
    - 作用：报警类型。
    - 方法数量：0
  - `AlarmRecord`（继承：SQLModel）
    - 作用：报警记录。
    - 方法数量：0
  - `ImageData`（继承：SQLModel）
    - 作用：行人截图数据。
    - 方法数量：0
  - `AudioData`（继承：SQLModel）
    - 作用：音频数据。
    - 方法数量：0
  - `MetalAnomaly`（继承：SQLModel）
    - 作用：金属异常信息。
    - 方法数量：0
  - `BMSData`（继承：SQLModel）
    - 作用：BMS 数据。
    - 方法数量：0
  - `RFIDData`（继承：SQLModel）
    - 作用：RFID 读卡数据。
    - 方法数量：0
  - `CablewayStatus`（继承：SQLModel）
    - 作用：索道 PLC 状态快照（通过 MQTT 上报）。
    - 方法数量：0
  - `CommandDirection`（继承：str, Enum）
    - 作用：命令方向。
    - 方法数量：0
  - `CommandLog`（继承：SQLModel）
    - 作用：命令历史。
    - 方法数量：0
  - `CommandStatus`（继承：str, Enum）
    - 作用：Command request status.
    - 方法数量：0
  - `CommandRequestState`（继承：SQLModel）
    - 作用：Track command request lifecycle by request_id.
    - 方法数量：0

## 4. 新人阅读建议（针对本文件）
- 建议先看公开函数/类，再沿“关键调用链”逐跳进入下层模块。
- 对 I/O 代码（MQTT、串口、DB）重点关注异常路径与重试策略。
- 可结合对应测试文件验证你对边界条件的理解。
