# 上位机说明（面向快速理解与运维）

## 1. 系统做什么（一句话）
上位机接收下位机通过 MQTT 上报的传感器/PLC/RFID/媒体数据，写入数据库并生成告警，同时提供 HTTP API 与 GUI 供查询与控制。

## 2. 运行环境与启动方式
- 操作系统：Windows（Conda 推荐 Python 3.11）。
- 依赖：MySQL、MQTT Broker（如 Mosquitto）。
- 可选：YOLO/音频依赖、FFmpeg/GStreamer。

快速启动（Conda 推荐）：
```powershell
server/scripts/conda_run.ps1 -EnvName "sensor_server" -Mode "stack"
```
```bat
server/scripts/conda_run.bat "sensor_server" "stack"
```
> 本地 RTSP 音频联调：
> - 当 `.env` 中 `AUDIO_ENABLED=true` 且 `AUDIO_RTSP_INPUT` 为 `rtsp://127.0.0.1:8554/audio` 或 `rtsp://localhost:8554/audio` 时，`server/scripts/conda_run.ps1` 会自动启动 `server/scripts/rtsp_audio_server.ps1`。
> - 如果 `conda` 不在 PATH 中，可给 `server/scripts/conda_run.ps1` 传入 `-CondaExe "C:\Users\...\anaconda3\Scripts\conda.exe"`，或先设置环境变量 `CONDA_EXE`。
> - 如需临时关闭该行为，可添加 `-SkipLocalRtspAudio`。
> - 可选的启动器专用 `.env` 键：`AUDIO_RTSP_SIM_SOURCE`、`AUDIO_RTSP_SIM_FILE`、`AUDIO_RTSP_SIM_LOOP`、`AUDIO_RTSP_SIM_FREQ`、`AUDIO_RTSP_SIM_SAMPLE_RATE`、`AUDIO_RTSP_SIM_CHANNELS`。
说明：
- 脚本会统一设置窗口为 UTF-8 输出，避免 API/GUI/MQTT 窗口乱码。
- GUI 外部播放依赖 `ffplay`，默认优先使用 Conda 环境内的 `ffplay.exe`，若不存在则使用系统 PATH 中的 `ffplay`。
仅 API：
```bash
conda run -n sensor_server python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

## 3. 非专业视角的数据流
1) 下位机采集数据 → MQTT 发布到对应主题（如 `sensors/data`）。
2) 上位机 MQTT 订阅 → 解析 → 入库 → 判断阈值 → 统一发布告警。
3) API/GUI 可以查询历史数据与当前状态。
4) 命令下发：API → MQTT → 下位机执行 → 回执 → 上位机记录状态。

历史分页接口补充：

## 4. 配置说明（`.env`）
- `MQTT_*`：MQTT 连接信息
- `MYSQL_*`：数据库连接信息
- `COMMAND_TIMEOUT_SECONDS`：命令超时判定
- `DATA_RETENTION_*`：数据保留策略（按天数/容量自动清理）
- `MEDIA_STORAGE_MODE`：媒体存库或落盘
- `MEDIA_GATEWAY_*`：媒体接入层（MediaMTX/go2rtc/custom）统一拉流入口
- `YOLO_*` / `AUDIO_*`：媒体服务配置
- `runtime_config`：运行期配置覆盖（通过 `/api/runtime-config` 更新）
> 建议 `API_HOST=0.0.0.0` 以便本机与局域网访问。

## 5. 目录结构（面向理解）
- `server/main.py`：FastAPI 启动入口。
- `server/app/api/`：HTTP API 路由与应用工厂。
- `server/app/mqtt/`：MQTT 连接与订阅分发。
- `server/app/services/`：入库/告警/音频/YOLO/清理等核心业务逻辑。
- `server/app/db/`：数据库模型与会话管理。
- `server/app/gui/`：Tkinter GUI。
- `server/app/schemas/`：API 请求/响应模型。
- `server/tests/`：单元测试。
- `server/scripts/`：启动与环境切换脚本。

## 6. 运行期配置热更新
- API：
  - `GET /api/runtime-config`：读取合并后的配置与覆盖状态
  - `PATCH /api/runtime-config`：提交运行期覆盖
  - `GET /api/runtime-config/options`：配置项说明
- MQTT：
  - `config/update`：上位机下发配置
  - `config/ack`：下位机回执（含 `pending_restart_keys`）
  - `device/hello`：下位机启动握手（携带 `config_version`）
- GUI：运行期配置页会从 `/api/runtime-config/options` 拉取 `.env` 中的全部配置项；热更新项即时生效，其他项自动触发 API 自重启（约 1s）。

## 7. 代码文件与函数说明（概要）
> 这里只给出模块级概览；完整的“逐文件/逐函数”说明见 `server/PLANNING.md`。

- `server/app/api/`：`create_app()` 负责构建 FastAPI 应用；`routes.py` 提供健康检查、数据查询、命令闭环等 API。
- `server/app/core/`：`Settings` 负责解析 `.env`；`MQTT_TOPICS` 定义主题常量。
- `server/app/db/`：SQLModel 表结构（传感器/BMS/RFID/PLC/媒体/告警/命令）；`session.py` 负责建表与兼容补列。
- `server/app/mqtt/`：`MQTTManager` 负责连接、订阅、发布、断线恢复。
- `server/app/services/`：
  - `data_service.py`：统一入库、阈值告警、命令闭环、数据保留清理。
  - `alarm_publisher.py` / `alarm_cache.py`：告警生成与缓存。
  - `bms_alerts.py`：单体低压告警。
  - `media_storage.py`：媒体落盘与读取（可选）。
  - `audio_service.py` / `audio_metrics.py` / `audio_worker.py`：音频采集、特征计算与告警。
  - `yolo_service.py` / `yolo_worker.py` / `rtsp_*`：视频检测与 RTSP 拉/推流。
  - `runtime_supervisor.py`：后台守护（MQTT/DB 重连、命令超时、数据清理）。
- `server/app/gui/`：`MonitoringGUI` 提供桌面界面（查询、命令、音频、PLC 控制）。
- `server/app/schemas/`：API 入参/出参模型。
- `server/tests/`：覆盖解析、告警、命令闭环、数据保留等核心逻辑。

## 8. 常见问题（FAQ）
- **为什么需要数据保留？** 避免数据库无限增长，保证长期稳定运行。
- **DB 不可用怎么办？** 上位机会发布降级告警，不阻塞 MQTT 消费。
- **媒体存库还是落盘？** 现场资源紧张建议 `filesystem`。

## 媒体与控制面隔离更新（2026-05）
- 新增 `media-write-worker`（异步后台 worker），`TelemetryBridgeServer` 收到 `telemetry.yolo.snapshot` / `telemetry.audio.clip` 后只做入队并快速 ACK。
- 新增独立 `media_db_worker`，媒体写入不再和普通 API 查询共用同一个 DB 队列。
- API `/api/images` 与 `/api/audio` 写入路径改为走 `media_db_worker`，降低媒体大对象写入对控制面查询接口的影响。
- 新增媒体网关接入（`MEDIA_GATEWAY_*`）：
  - `RuntimeSupervisor` 可守护外部媒体网关进程（`MEDIA_GATEWAY_EXEC` + `MEDIA_GATEWAY_ARGS`）。
  - `YOLO` / `Audio` 默认可自动改用 `MEDIA_GATEWAY_RELAY_RTSP` 作为单入口拉流。
  - `/api/health/workers` 提供 `media_gateway` 状态、直连输入与生效输入对照、`control-panel` 建议拉流地址。

## PLC Control Update

- Cableway PLC control is now server-only. `/api/cableway/command` no longer falls back to MQTT forwarding.
- The server reads these status registers directly over Modbus TCP: `VD2244`, `VD2248`, `VD2252`, `VW2432`, `V2889.7`, `V2909.0`, `V2909.1`.
- Detailed fault registers are queried only after `GZ total fault (V2889.7)` is active.
- Control commands (`control` / `estop`) are executed through a higher-priority worker queue than background polling.

## PLC Timing Trace

- Enable end-to-end PLC timing trace in `.env`:
  - `PLC_TIMING_TRACE_ENABLED=true`
  - `PLC_TIMING_TRACE_LOG=logs/plc_timing.log`
  - `PLC_TIMING_TRACE_INCLUDE_POLL=true`
  - `PLC_TIMING_TRACE_INCLUDE_HEADERS=true`
- The trace records the actual control path:
  - `gui.click`
  - `api.route_enter`
  - `svc.enqueue`
  - `svc.dequeue`
  - `plc.control_start`
  - `modbus.tx_send`
- To analyze the latest command:
```powershell
$env:PYTHONPATH='C:\Users\lpb\Desktop\loonginx\server'
python C:\Users\lpb\Desktop\loonginx\server\scripts\analyze_plc_timing.py --last 1
```
- To analyze a specific request:
```powershell
$env:PYTHONPATH='C:\Users\lpb\Desktop\loonginx\server'
python C:\Users\lpb\Desktop\loonginx\server\scripts\analyze_plc_timing.py --request-id <request_id>
```
- The primary diagnosis metrics are:
  - `click_to_tx_send_ms`: operator click to actual Modbus TX send
  - `db_prelog_ms`: API pre-log database latency
  - `queue_wait_ms`: time waiting inside `plc_rt` before the command is dequeued
  - `connect_ms`: Modbus reconnect/build-connection latency
  - `plc_response_ms`: Modbus TX to RX latency
