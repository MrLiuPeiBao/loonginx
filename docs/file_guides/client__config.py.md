# 文件讲解：`client/config.py`

## 1. 文件定位
- **角色**：业务/基础模块
- **所在层级**：`client`
- **是否建议新人优先阅读**：是
- **模块文档摘要**：Configuration for the sensor gateway.

## 2. 对外依赖与协作关系
- **导入依赖（节选）**：
  - `json`
  - `os`
  - `from utils.config_override import load_overrides`

## 3. 核心模块与实现原理
- **关键常量**：`BMS_CONFIG, BMS_POLL_INTERVAL, LOOP_IDLE_DELAY, MAX_SENSOR_ATTEMPTS, MESSAGE_SCHEMA_VERSION, MQTT_CONFIG, MQTT_DEGRADE_ENTER_SECONDS, MQTT_DEGRADE_EXIT_SECONDS, MQTT_DEGRADE_FACTOR, MQTT_OFFLINE_POLICY, MQTT_TOPICS, MQTT_TOPIC_QOS_MAP, PLC_CONFIG, RFID_SERIAL_CONFIG, SENSOR_CONFIGS, SENSOR_POLL_DELAY, SENSOR_RETRY_DELAY, SERIAL_CONFIG, SERIAL_DIRECT_RESPONSE_DELAY, SERIAL_DIRECT_RETRIES`
- **函数设计**：
  - `_get_env(name: str, default: object)`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：os.getenv
  - `_get_int_env(name: str, default: int)`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：_get_env, int
  - `_get_float_env(name: str, default: float)`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：_get_env, float
  - `_get_bool_env(name: str, default: bool)`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：_get_env, lower, str, strip
  - `_get_str_env(name: str, default: str)`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：_get_env, str
  - `_get_json_or_kv_env(name: str, default: dict)`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：_get_env, int, isinstance, item.split, json.loads, key.strip, parsed.items, raw.split
  - `_cast_bool(value: object, default: bool)`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：isinstance, lower, str, strip
  - `_apply_override(key: str, current: object, caster)`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：_RUNTIME_OVERRIDES.get, _cast_bool, bool, caster

## 4. 新人阅读建议（针对本文件）
- 建议先看公开函数/类，再沿“关键调用链”逐跳进入下层模块。
- 对 I/O 代码（MQTT、串口、DB）重点关注异常路径与重试策略。
- 可结合对应测试文件验证你对边界条件的理解。
