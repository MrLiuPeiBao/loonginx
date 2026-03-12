# 文件讲解：`client/sensors/sensor_factory.py`

## 1. 文件定位
- **角色**：业务/基础模块
- **所在层级**：`client/sensors`
- **是否建议新人优先阅读**：是

## 2. 对外依赖与协作关系
- **导入依赖（节选）**：
  - `logging`
  - `from typing import Optional`
  - `from .bms_sensors import BMSSensor`
  - `from .gas_sensors import CH4Sensor, COSensor, H2SSensor, O2Sensor, SmokeSensor`
  - `from .temperature_sensor import HumiditySensor, PressureSensor, TemperatureSensor`

## 3. 核心模块与实现原理
- **类设计**：
  - `SensorFactory`（继承：无）
    - 作用：Create sensor instances based on configuration.
    - 方法数量：1
    - `create_sensor(cls, sensor_type: str, serial_manager, config: Optional[dict] = None)`
      - 关键调用链：cls.SENSOR_TYPES.get, logging.error, logging.warning, sensor_cls

## 4. 新人阅读建议（针对本文件）
- 建议先看公开函数/类，再沿“关键调用链”逐跳进入下层模块。
- 对 I/O 代码（MQTT、串口、DB）重点关注异常路径与重试策略。
- 可结合对应测试文件验证你对边界条件的理解。
