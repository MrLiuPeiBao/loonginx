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
- `/api/sensors`、`/api/bms`、`/api/rfid`、`/api/commands`、`/api/cableway/status`、`/api/command-requests`、`/api/alarms`、`/api/images`、`/api/audio`、`/api/metal-anomaly` 返回 `{ "total": 总条数, "items": 当前页数据 }`，便于前端显示分页总数。

## 4. 配置说明（`.env`）
- `MQTT_*`：MQTT 连接信息
- `MYSQL_*`：数据库连接信息
- `COMMAND_TIMEOUT_SECONDS`：命令超时判定
- `DATA_RETENTION_*`：数据保留策略（按天数/容量自动清理）
- `MEDIA_STORAGE_MODE`：媒体存库或落盘
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
  - `ingestion.py` / `cableway_ingestion.py`：MQTT 消息解析与入库入口。
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

## 9. 虚拟 Client 联调脚本
- 适用场景：没有下位机硬件时，在开发机上持续模拟 MQTT 上报、命令回执、配置回执，以及图片/音频/金属异常等数据。
- 脚本位置：`server/scripts/virtual_client.py`
- 推荐启动方式（使用 Conda 环境）：
```powershell
conda run -n sensor_server python "server/scripts/virtual_client.py"
```
- 常用参数：
  - `--mqtt-broker localhost --mqtt-port 1883`：指定 Broker
  - `--api-base-url http://127.0.0.1:8000/api`：指定上位机 API
  - `--duration-seconds 60`：只运行一段时间后自动退出，便于自测
  - `--no-http-seed`：只模拟 MQTT，不通过 HTTP 补充图片/音频/金属异常
- 脚本默认会：
  - 周期发布 `sensors/data`、`sensors/bms`、`sensors/rfid`、`cableway/status`、`device/hello`
  - 订阅并响应 `sensors/command/request`、`cableway/command/request`、`config/update`
  - 通过 API 初始化传感器阈值，并写入示例图片、音频、金属异常数据
