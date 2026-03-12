# 文件讲解：`client/sensors/base_sensor.py`

## 1. 文件定位
- **角色**：业务/基础模块
- **所在层级**：`client/sensors`
- **是否建议新人优先阅读**：是

## 2. 对外依赖与协作关系
- **导入依赖（节选）**：
  - `from __future__ import annotations`
  - `from abc import ABC, abstractmethod`
  - `from datetime import datetime`
  - `from typing import Any, Optional`

## 3. 核心模块与实现原理
- **类设计**：
  - `BaseSensor`（继承：ABC）
    - 作用：Abstract base class for all concrete sensors.
    - 方法数量：6
    - `__init__(self, name: str, address: int, serial_manager)`
    - `read_data(self)`
      - 职责：Fetch raw data from the device.
    - `parse_data(self, raw_data)`
      - 职责：Convert raw device data to business-friendly format.
    - `get_formatted_data(self)`
      - 职责：Return a unified data payload for the current sensor reading.
      - 关键调用链：self._get_timestamp_pair, self.parse_data, self.read_data
    - `_get_timestamp()`
      - 关键调用链：BaseSensor._get_timestamp_pair
    - `_get_timestamp_pair()`
      - 关键调用链：astimezone, datetime.now, now.isoformat, now.timestamp

## 4. 新人阅读建议（针对本文件）
- 建议先看公开函数/类，再沿“关键调用链”逐跳进入下层模块。
- 对 I/O 代码（MQTT、串口、DB）重点关注异常路径与重试策略。
- 可结合对应测试文件验证你对边界条件的理解。
