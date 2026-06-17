# 文件讲解：`client/sensors/gas_sensors.py`

## 1. 文件定位
- **角色**：业务/基础模块
- **所在层级**：`client/sensors`
- **是否建议新人优先阅读**：是

## 2. 对外依赖与协作关系
- **导入依赖（节选）**：
  - `from __future__ import annotations`
  - `logging`
  - `from .base_sensor import BaseSensor`
  - `from utils.data_parser import DataParser`

## 3. 核心模块与实现原理
- **类设计**：
  - `GasSensor`（继承：BaseSensor）
    - 作用：Generic gas and smoke sensor parsing register payloads.
    - 方法数量：3
    - `__init__(self, name: str, serial_manager, config: dict)`
      - 关键调用链：__init__, config.get, lower, super
    - `read_data(self)`
      - 关键调用链：self.serial_manager.read_registers
    - `parse_data(self, raw_data)`
      - 关键调用链：DataParser.parse_dcba, DataParser.parse_o2, DataParser.parse_raw, DataParser.parse_smoke, join, logging.warning
  - `COSensor`（继承：GasSensor）
    - 方法数量：1
    - `__init__(self, serial_manager, config: dict)`
      - 关键调用链：__init__, super
  - `H2SSensor`（继承：GasSensor）
    - 方法数量：1
    - `__init__(self, serial_manager, config: dict)`
      - 关键调用链：__init__, super
  - `O2Sensor`（继承：GasSensor）
    - 方法数量：1
    - `__init__(self, serial_manager, config: dict)`
      - 关键调用链：__init__, super
  - `CH4Sensor`（继承：GasSensor）
    - 方法数量：1
    - `__init__(self, serial_manager, config: dict)`
      - 关键调用链：__init__, super
  - `SmokeSensor`（继承：GasSensor）
    - 方法数量：1
    - `__init__(self, serial_manager, config: dict)`
      - 关键调用链：__init__, super

## 4. 新人阅读建议（针对本文件）
- 建议先看公开函数/类，再沿“关键调用链”逐跳进入下层模块。
- 对 I/O 代码（MQTT、串口、DB）重点关注异常路径与重试策略。
- 可结合对应测试文件验证你对边界条件的理解。
