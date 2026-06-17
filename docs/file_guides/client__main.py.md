# 文件讲解：`client/main.py`

## 1. 文件定位
- **角色**：入口文件
- **所在层级**：`client`
- **是否建议新人优先阅读**：是

## 2. 对外依赖与协作关系
- **导入依赖（节选）**：
  - `ast`
  - `asyncio`
  - `json`
  - `logging`
  - `time`
  - `from datetime import datetime`
  - `from typing import Any, Dict, Optional, Tuple`
  - `from communication.cableway_plc import CablewayPLC, CablewayPLCConfig`
  - `from communication.mqtt_client import MQTTClient`
  - `from communication.plc_logging import get_plc_logger`
  - `from communication.rfid_reader import RFIDReader`
  - `from communication.serial_manager import SerialManager`
  - `from config import BMS_CONFIG, MQTT_CONFIG, MQTT_TOPICS, PLC_CONFIG, RFID_SERIAL_CONFIG, SENSOR_CONFIGS, SERIAL_CONFIG, MESSAGE_SCHEMA_VERSION, SENSOR_POLL_DELAY, SENSOR_RETRY_DELAY, MAX_SENSOR_ATTEMPTS, LOOP_IDLE_DELAY, BMS_POLL_INTERVAL, MQTT_DEGRADE_ENTER_SECONDS, MQTT_DEGRADE_EXIT_SECONDS, MQTT_DEGRADE_FACTOR`
  - `from sensors.sensor_factory import SensorFactory`
  - `from utils.degraded_state import update_degraded_state`
  - `from utils.message_envelope import with_payload_type`
  - `from utils.config_override import CONFIG_UPDATED_AT_KEY, CONFIG_VERSION_KEY, get_config_updated_at, get_config_version, load_overrides, merge_overrides, save_overrides, split_hot_and_restart`

## 3. 核心模块与实现原理
- **类设计**：
  - `SensorGateway`（继承：无）
    - 方法数量：23
    - `__init__(self)`
      - 关键调用链：MQTTClient, RFIDReader, SerialManager, float, max, self._init_cableway_plc
    - `_setup_logging()`
      - 关键调用链：handlers.insert, logging.FileHandler, logging.StreamHandler, logging.basicConfig, print
    - `initialize_sensors(self)`
      - 关键调用链：SENSOR_CONFIGS.items, SensorFactory.create_sensor, logging.error, logging.info
    - `setup_mqtt_callbacks(self)`
      - 关键调用链：plc_logger.info, self.mqtt_client.subscribe
    - `handle_command(self, payload: bytes)`
      - 关键调用链：bool, logging.error, logging.info, self._format_hex, self._get_timestamp, self._get_ts
    - `handle_cableway_command(self, payload: bytes)`
      - 关键调用链：MQTT_CONFIG.get, RuntimeError, ValueError, bool, data.get, float
    - `handle_config_update(self, payload: bytes)`
      - 关键调用链：MQTT_CONFIG.get, ValueError, data.get, int, isinstance, json.loads
    - `_apply_hot_overrides(self, overrides: Dict[str, Any])`
      - 关键调用链：_apply_float, _apply_int, applied.append, applied.extend, float, int
    - ... 其余 15 个方法建议在 IDE 中按调用层级继续追踪

## 4. 新人阅读建议（针对本文件）
- 建议先看公开函数/类，再沿“关键调用链”逐跳进入下层模块。
- 对 I/O 代码（MQTT、串口、DB）重点关注异常路径与重试策略。
- 可结合对应测试文件验证你对边界条件的理解。
