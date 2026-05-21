# 下位机规划与实现说明

## 1. 目标与边界
- **目标**：稳定采集传感器/BMS/RFID 数据并上报，接收并执行上位机串口命令回执。
- **边界**：不负责数据持久化与告警逻辑；只负责采集、封装、传输。
- **资源约束**：loongarch64（小端）环境，CPU/内存/电力有限，所有策略以轻量为原则。
- **兼容性**：消息结构保持与旧版本兼容，新增字段不影响旧端。

## 2. 运行流程
1) 初始化串口、MQTT、传感器对象、RFID。
2) 进入主循环：轮询传感器 → 解析 → 上报 MQTT。
3) 独立处理：RFID 读卡上报。
4) 接收上位机命令 → 串口执行 → 回执上报。
5) MQTT 断连时走离线缓存，断连过久自动降频。

## 3. 数据与消息结构
- 所有上报消息统一包含：`timestamp`（ISO 字符串）、`ts`（毫秒时间戳）、`schema`（结构版本）。
- 新增字段 `payload_type` 用于区分消息类型（sensor_data/bms_data/rfid_data/command_response 等）。

## 4. 代码文件与函数说明（逐文件）

### 4.1 `client/main.py`
**角色**：下位机主入口，封装“采集 → 上报 → 命令处理”的完整闭环。

- `SensorGateway.__init__()`：初始化日志、串口、MQTT、RFID 与内部状态。
- `SensorGateway._setup_logging()`：配置控制台/文件日志。
- `SensorGateway.initialize_sensors()`：根据配置创建各类传感器对象。
- `SensorGateway.setup_mqtt_callbacks()`：订阅命令主题并绑定回调。
- `SensorGateway.handle_command()`：处理串口命令下发（原始 Modbus 命令）。
- `SensorGateway.handle_rfid_data()`：RFID 读卡数据封装并上报。
- `SensorGateway._parse_command_payload()`：解析命令 payload（支持 JSON/十六进制）。
- `SensorGateway._parse_command_text()`：解析命令文本形式。
- `SensorGateway._format_hex()`：格式化字节为十六进制字符串。
- `SensorGateway._get_timestamp()` / `_get_ts()`：生成 ISO 时间戳与毫秒时间戳。
- `SensorGateway._update_degraded_state()`：根据 MQTT 状态更新降频模式。
- `SensorGateway.read_sensors_loop()`：周期性读取所有传感器并批量上报。
- `SensorGateway._read_sensor_with_retries()`：单传感器重试读取与延迟控制。
- `SensorGateway._run_tasks()`：并发启动传感器轮询与 RFID 读卡。
- `SensorGateway.start()`：运行主循环、连接 MQTT、启动任务。
- `SensorGateway.stop()`：停止任务与释放资源。

### 4.2 `client/config.py`
**角色**：统一读取 `.env` 并生成所有配置字典。

- `_get_env()` / `_get_int_env()` / `_get_float_env()` / `_get_bool_env()` / `_get_str_env()`：读取并转换环境变量。
- `_get_json_or_kv_env()`：解析 JSON 或 `key=value` 形式配置（如 QoS 映射）。
- `MQTT_CONFIG`：MQTT 连接、QoS、离线队列、离线策略等。
- `MQTT_TOPIC_QOS_MAP` / `MQTT_OFFLINE_POLICY`：对外暴露的主题 QoS 与离线策略。
- `MQTT_TOPICS`：统一主题字典。
- `SERIAL_CONFIG` / `RFID_SERIAL_CONFIG`：RS485 与 RFID 串口配置。
- `SENSOR_CONFIGS` / `BMS_CONFIG`：传感器与 BMS 寄存器配置。
- `MESSAGE_SCHEMA_VERSION` / `SENSOR_POLL_DELAY` / `BMS_POLL_INTERVAL` 等：轮询节奏与消息版本控制。

### 4.3 `client/communication/mqtt_client.py`
**角色**：轻量 MQTT 下位机封装，支持离线缓存与按 topic QoS。

- `MQTTClient.__init__()`：初始化 MQTT 下位机与离线队列。
- `MQTTClient._on_connect()` / `_on_disconnect()`：连接状态回调。
- `MQTTClient._on_message()`：订阅消息回调分发。
- `MQTTClient.connect()` / `disconnect()`：连接与断开。
- `MQTTClient.publish()`：发布消息（支持离线缓存）。
- `MQTTClient.subscribe()`：订阅主题并注册回调。
- `MQTTClient.ensure_connected()`：确保连接状态。
- `MQTTClient._format_payload_for_log()`：日志友好输出。
- `MQTTClient._resolve_qos()`：按 topic 覆盖 QoS。
- `MQTTClient._enqueue_offline()` / `_enqueue_offline_latest()`：离线缓存策略。
- `MQTTClient._drain_offline_queue()`：连接恢复后批量发送离线消息。

### 4.4 `client/communication/serial_manager.py`
**角色**：统一 RS485/Modbus RTU 通信与原始命令发送。

- `SerialManager.__init__()`：初始化串口参数与缓存。
- `SerialManager._get_or_create_instrument()` / `get_instrument()`：获取/创建 Modbus 设备实例。
- `SerialManager.read_registers()`：标准寄存器读取（使用 minimalmodbus）。
- `SerialManager.read_registers_direct()`：直发帧读取（可调整重试/超时）。
- `SerialManager.send_raw_command()`：发送原始 Modbus 帧并等待响应。
- `_perform_direct_request()` / `_read_register_response()`：直发请求与响应解析。
- `_extract_registers_from_frame()`：从 RTU 帧解析寄存器值。
- `_estimate_response_length()`：根据寄存器数量估算响应长度。
- `close_all()`：关闭所有串口设备。
- `_validate_crc()` / `_calculate_crc()`：CRC 校验。
- `_build_read_request()`：构造读寄存器请求帧。
- `_read_exact()` / `_drain_serial()`：串口读取与缓冲清理。

### 4.5 `client/communication/rfid_reader.py`
**角色**：RFID 串口读卡线程。

- `RFIDReader.__init__()`：初始化 RFID 串口。
- `RFIDReader.start_reading()` / `stop_reading()`：启动/停止读取线程。
- `RFIDReader._read_loop()`：循环读取卡号并回调上报。
- `RFIDReader._get_timestamp()` / `_get_ts()`：生成时间戳。
- `RFIDReader.is_reading()`：读取状态。

### 4.6 `client/sensors/base_sensor.py`
**角色**：传感器基类，统一读取/解析/格式化。

- `BaseSensor.__init__()`：保存串口对象与配置。
- `BaseSensor.read_data()`：读取寄存器值。
- `BaseSensor.parse_data()`：解析寄存器原始值。
- `BaseSensor.get_formatted_data()`：生成可上报结构。
- `BaseSensor._get_timestamp()` / `_get_timestamp_pair()`：时间戳辅助。

### 4.7 `client/sensors/temperature_sensor.py`
**角色**：温湿度/压力传感器实现。

- `_EnvironmentSensor.__init__()` / `read_data()` / `parse_data()`：环境类传感器通用逻辑。
- `TemperatureSensor` / `HumiditySensor` / `PressureSensor`：具体类型包装。

### 4.8 `client/sensors/gas_sensors.py`
**角色**：气体/烟雾传感器实现。

- `GasSensor.__init__()` / `read_data()` / `parse_data()`：气体通用解析。
- `COSensor` / `H2SSensor` / `O2Sensor` / `CH4Sensor` / `SmokeSensor`：具体类型包装。

### 4.9 `client/sensors/bms_sensors.py`
**角色**：BMS 电池参数读取与解析。

- `BMSSensor.__init__()`：加载寄存器配置。
- `BMSSensor.read_all_data()`：批量读取电池参数。
- `BMSSensor.read_data()`：单项读取。
- `BMSSensor.parse_data()`：解析与单位换算。

### 4.10 `client/sensors/sensor_factory.py`
**角色**：传感器工厂，根据类型创建实例。

- `SensorFactory.create_sensor()`：按传感器类型返回具体实例。

### 4.11 `client/utils/data_parser.py`
**角色**：寄存器值解析工具。

- `DataParser.parse_dcba()`：解析 DCBA 字节序的 32 位浮点。
- `DataParser.parse_raw()`：原始数值直出。
- `DataParser.parse_o2()`：O2 特殊倍率解析。
- `DataParser.parse_smoke()`：烟雾特殊倍率解析。
- `DataParser.parse_bms_data()`：BMS 复合数据解析。

### 4.12 `client/utils/degraded_state.py`
**角色**：MQTT 断连降频状态机。

- `update_degraded_state()`：根据连接/断连时长进入或退出降频。

### 4.13 `client/utils/message_envelope.py`
**角色**：统一补齐消息字段。

- `with_payload_type()`：写入 `payload_type` 字段。

### 4.14 `client/tests/*`
**角色**：关键逻辑的单元测试，保证兼容性与稳定性。

- `test_config_parsing.py`：MQTT QoS 映射、离线策略解析。
- `test_degraded_state.py`：降频进入/退出逻辑。
- `test_message_envelope.py`：`payload_type` 补齐。
- `test_mqtt_client_offline.py`：离线队列与 `latest` 策略。
- `test_serial_manager_env.py`：串口直发参数来自环境变量。
## 5. 维护与扩展建议（保证轻量）
- 传感器扩展优先复用 `SensorFactory` 与 `BaseSensor`，避免重复逻辑。
- 断连处理优先通过 `MQTT_OFFLINE_POLICY` 与降频参数调优，不引入复杂组件。
- 仅在现场确需时引入新依赖，避免 loongarch64 环境维护成本。
