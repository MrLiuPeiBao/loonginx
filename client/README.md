# 下位机说明

## 1. 系统做什么（一句话）
下位机负责在 loongarch64 设备上轮询传感器/PLC/RFID，封装成统一消息，通过 MQTT 上报到上位机，并接收上位机下发的控制命令回执。

## 2. 运行环境与启动方式
- 运行环境：loongarch64 GNU/Linux（小端），Python 3.9。
- 依赖：`minimalmodbus`、`pyserial`、`paho-mqtt`、`python-dotenv`（可选，用于加载 `.env`）。
- 资源限制：CPU/内存/电力有限，下位机逻辑尽量轻量、低开销。

启动方式：
```bash
cd "client"
python3.9 "main.py"
```

## 3. 非专业视角的数据流
1) 传感器/BMS/PLC/RFID 通过串口/Modbus 读取原始值。
2) 统一封装为消息（含时间戳、schema 版本、payload_type）。
3) 按主题发布 MQTT（如 `sensors/data`、`cableway/status`）。
4) 接收上位机控制命令 → 串口/PLC 执行 → 回执上报。
5) MQTT 断连时可离线缓存；断连过久自动进入降频模式，降低资源消耗。

对接提示：
- 上位机历史查询接口 `/api/sensors`、`/api/bms`、`/api/rfid`、`/api/commands`、`/api/cableway/status`、`/api/command-requests`、`/api/alarms`、`/api/images`、`/api/audio`、`/api/metal-anomaly` 返回 `{ "total": 总条数, "items": 当前页数据 }`。

## 4. 配置说明（`client/.env`）
> 以下为常用项，实际以 `client/config.py` 为准。

**MQTT 连接与离线策略**
- `MQTT_BROKER` / `MQTT_PORT` / `MQTT_CLIENT_ID`
- `MQTT_USERNAME` / `MQTT_PASSWORD`
- `MQTT_KEEPALIVE` / `MQTT_PUBLISH_QOS`
- `MQTT_TOPIC_QOS_MAP`：按 topic 设定 QoS（支持 JSON 或 `topic=qos` 逗号格式）。
- `MQTT_OFFLINE_QUEUE_ENABLED` / `MQTT_OFFLINE_QUEUE_MAX_ITEMS` / `MQTT_OFFLINE_QUEUE_MAX_BYTES`
- `MQTT_OFFLINE_POLICY`：`queue`（完整队列）或 `latest`（每 topic 仅保留最新）。

**降频策略（资源友好）**
- `MQTT_DEGRADE_ENTER_SECONDS`：连续断连多久后进入降频。
- `MQTT_DEGRADE_EXIT_SECONDS`：稳定连接多久后退出降频。
- `MQTT_DEGRADE_FACTOR`：降频倍率（>1 时拉长轮询间隔）。

**串口/Modbus**
- `SERIAL_PORT` / `SERIAL_BAUDRATE` / `SERIAL_TIMEOUT`
- `SERIAL_DIRECT_RETRIES` / `SERIAL_DIRECT_RESPONSE_DELAY` / `SERIAL_DIRECT_TIMEOUT`
- `RFID_SERIAL_PORT` / `RFID_SERIAL_BAUDRATE`

**PLC（索道）**
- `PLC_ENABLED` / `PLC_HOST` / `PLC_PORT` / `PLC_UNIT_ID`
- `PLC_TIMEOUT` / `PLC_CONNECT_TIMEOUT`
- `PLC_STATUS_POLL_INTERVAL` / `PLC_HEARTBEAT_INTERVAL` / `PLC_COMMAND_PULSE_SECONDS`
- `PLC_EVEN_BYTE_IS_HIGH` / `PLC_FLOAT_WORD_ORDER` / `PLC_FLOAT_BYTE_ORDER`

**轮询节奏**
- `SENSOR_POLL_DELAY` / `SENSOR_RETRY_DELAY` / `MAX_SENSOR_ATTEMPTS`
- `LOOP_IDLE_DELAY` / `BMS_POLL_INTERVAL`
- `MESSAGE_SCHEMA_VERSION`

**运行期覆盖文件**
- `client/config_override.json`：下位机热更新覆盖文件（可由上位机下发）。

## 5. 运行期配置热更新
- 下位机启动发送 `device/hello`（携带 `config_version`/`last_ts`）。
- 接收 `config/update` 后：
  - 可热更项即时生效（轮询间隔、降频、离线策略等）。
  - 需重启项写入 `config_override.json` 并回执 `pending_restart_keys`。
- 可热更示例：`SENSOR_POLL_DELAY`、`BMS_POLL_INTERVAL`、`MQTT_DEGRADE_*`、`MQTT_OFFLINE_*`
- 需重启示例：`MQTT_BROKER`、`MQTT_PORT`、`MQTT_CLIENT_ID`

## 6. 目录结构（面向理解）
- `client/main.py`：主入口与主循环（采集/上报/命令处理）。
- `client/config.py`：环境变量解析与默认配置。
- `client/communication/`：MQTT、串口、PLC、RFID 适配层。
- `client/sensors/`：具体传感器驱动与解析逻辑。
- `client/utils/`：解析/降级/消息封装等小工具。
- `client/tests/`：单元测试。

## 7. 代码文件与函数说明（概要）
> 这里只给出概览；完整的“逐文件/逐函数”说明见 `client/PLANNING.md`。

- `client/main.py`：`SensorGateway` 负责初始化、轮询、上报、命令回执与 PLC 状态采集。
- `client/config.py`：`.env` 读取与默认配置；提供 MQTT/串口/PLC/BMS 等配置字典。
- `client/communication/mqtt_client.py`：MQTT 连接、订阅、发布、离线队列管理与 QoS 映射。
- `client/communication/serial_manager.py`：统一 Modbus RTU 读写与原始命令发送，带 CRC 校验。
- `client/communication/cableway_plc.py`：PLC Modbus TCP 读写与心跳/脉冲控制。
- `client/communication/rfid_reader.py`：RFID 串口读卡循环。
- `client/sensors/*`：温湿度/气体/烟雾/BMS 等传感器读取与解析。
- `client/utils/*`：解析函数、降级状态机、消息字段补齐。

## 8. 常见问题（FAQ）
- **设备资源紧张怎么办？** 使用 `MQTT_DEGRADE_*` 与 `SENSOR_*` 间隔配置，降低轮询频率。
- **MQTT 断链会丢数据吗？** 可开启离线缓存，`latest` 模式只保留每主题最新数据。
- **PLC 字节序不一致怎么办？** 调整 `PLC_EVEN_BYTE_IS_HIGH` / `PLC_FLOAT_WORD_ORDER` / `PLC_FLOAT_BYTE_ORDER`。
