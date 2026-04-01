# PLC 测试指南

## 范围
验证上位机与下位机 PLC 功能：命令流、状态入库、日志记录与故障隔离。

## 前置条件
- 上位机：Windows + Conda 运行 FastAPI + MQTT。
- 下位机：loongarch64 GNU/Linux MIPS64 R2 架构，Python 3.9。
- PLC 可达（Modbus TCP）或准备不可达 IP 用于异常测试。
- MQTT Broker 可用。

## 日志位置
- 下位机 PLC 日志：`client/logs/plc.log`
- 上位机 PLC 日志：`server/logs/plc.log`

## 测试矩阵

### 1) PLC 禁用（下位机）
**目标**：PLC 异常不影响其他功能。
**步骤**：
1. 在 `client/config.py` 设置 `PLC_CONFIG.enabled = false`。
2. 启动下位机。
3. 验证传感器与 RFID 仍正常上报。
**预期**：
- PLC 轮询不启动。
- 传感器/RFID 功能持续运行。

### 2) PLC 不可达（下位机）
**目标**：PLC 连接错误被隔离。
**步骤**：
1. 将 `PLC_CONFIG.host` 设为不可路由 IP。
2. 启动下位机。
3. 观察传感器与 RFID 数据仍可发布。
**预期**：
- `client/logs/plc.log` 记录连接错误。
- 其他功能不受影响。

### 3) PLC 状态轮询（下位机）
**目标**：状态轮询与 MQTT 发布正常。
**步骤**：
1. `PLC_CONFIG.host` 设为真实 PLC IP。
2. 启动下位机。
3. 观察 MQTT 主题 `cableway/status`。
**预期**：
- 按轮询间隔发布状态。
- 日志包含心跳与寄存器读写。

### 4) PLC 命令：控制（上位机 -> 下位机）
**目标**：命令可下发并执行。
**步骤**：
1. 发送 API 请求：
   ```json
   POST /api/cableway/command
   {
     "type": "control",
     "command_code": 101,
     "device_id": "<gateway_id>",
     "request_id": "test-control-001"
   }
   ```
2. 观察 MQTT `cableway/command/request`。
3. 观察 MQTT `cableway/command/response`。
4. 查询命令状态：`GET /api/command-requests/test-control-001`。
**预期**：
- 下位机执行命令并回执。
- 上位机命令状态变更为 `ack`。

### 5) PLC 命令：急停
**步骤**：
```json
POST /api/cableway/command
{
  "type": "estop",
  "device_id": "<gateway_id>",
  "request_id": "test-estop-001"
}
```
**预期**：
- 记录急停脉冲写入与复位。

### 6) PLC 命令：设置参数
**步骤**：
```json
POST /api/cableway/command
{
  "type": "set_params",
  "params": { "cs_auto_speed": 0.4 },
  "device_id": "<gateway_id>",
  "request_id": "test-params-001"
}
```
**预期**：
- 参数校验通过并写入 PLC。

### 7) 命令超时验证
**目标**：命令未回执时标记 timeout。
**步骤**：
1. 设置 `.env`：`COMMAND_TIMEOUT_SECONDS=15`。
2. 下发命令但断开下位机 MQTT。
3. 查询 `GET /api/command-requests/<request_id>`。
**预期**：
- 状态变为 `timeout`。

### 8) 上位机入库与 API 查询
**目标**：状态入库与查询正常。
**步骤**：
1. 确保下位机发布 `cableway/status`。
2. 调用：
   - `GET /api/cableway/status?limit=1`
   - `GET /api/cableway/status/latest`
**预期**：
- 返回 `device_id` / `plc_host` / `status`。

### 9) 故障隔离（上位机）
**目标**：PLC 入库异常不影响其他接口。
**步骤**：
1. 停止 MQTT 或发送畸形载荷。
2. 调用 `GET /api/health`。
**预期**：
- 其他接口仍可用。

## 备注
- 命令状态依赖 `request_id`，建议所有测试请求都带 `request_id`。
- 单设备场景可省略 `device_id`，多设备场景建议显式传递。
