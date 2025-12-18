# 项目说明

## 概述
本项目负责工业安全监测平台的服务器端，实现以下能力：
- 采集并存储多类传感器（环境、BMS、RFID 等）数据
- 提供 FastAPI REST 接口，支持数据查询、写入以及阈值配置管理
- 通过 MQTT 与现场设备互通，完成命令下发与回执记录
- 提供 Tkinter 图形界面，便于本地监控与运维

## 目录结构
```
app/
  api/        # FastAPI 路由与应用工厂
  core/       # 配置、常量与环境加载
  db/         # SQLModel 模型与会话管理
  mqtt/       # MQTT 客户端封装与消息回调
  schemas/    # Pydantic/SQLModel 数据模型
  services/   # 业务服务与入库逻辑
  gui/        # Tkinter 客户端
main.py       # FastAPI 入口（uvicorn）
```

## 环境准备
1. 使用 Python 3.9，并创建约定的虚拟环境：
   ```bash
   python3 -m venv venv_widows
   source venv_widows/bin/activate
   ```
2. 安装依赖：
   ```bash
   python -m pip install -r requirements.txt
   ```

## 配置说明
- 建议通过 `.env` 管理环境变量（示例）：
  ```
  APP_NAME=sensor_server
  GUI_ENABLED=true
  MQTT_BROKER=localhost
  MQTT_PORT=1883
  MQTT_USERNAME=
  MQTT_PASSWORD=
  MYSQL_HOST=localhost
  MYSQL_PORT=3306
  MYSQL_USER=root
  MYSQL_PASSWORD=root
  MYSQL_DATABASE=loognix
  API_HOST=0.0.0.0
  API_PORT=8000
  ```
- 应用启动时会调用 `python_dotenv.load_dotenv()` 自动加载上述配置。

## 启动 FastAPI 服务
```bash
source venv_widows/bin/activate
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```
启动后可通过 `http://<host>:8000/docs` 访问 Swagger UI。

## 核心 API
- `GET /api/health`：健康检查（包含 `db_ok`、`mqtt_ok` 等状态字段）
- `GET /api/health/live`：liveness（进程存活）
- `GET /api/health/ready`：readiness（DB+MQTT 就绪，未就绪返回 503，便于 watchdog 判定是否需要重启）
- 传感器数据：`GET/POST /api/sensors`、`GET /api/sensors/latest`
- BMS 数据：`GET/POST /api/bms`
- RFID 数据：`GET/POST /api/rfid`
- 图像数据：`GET/POST /api/images`
- 音频数据：`GET/POST /api/audio`
- 金属异常：`GET/POST /api/metal-anomaly`
- 命令日志：`GET /api/commands`、`POST /api/commands`（发布到 MQTT 并记录往返日志）
- 传感器阈值配置：`GET/POST /api/config/sensors`、`PUT/DELETE /api/config/sensors/{type}`
- 报警记录：`GET /api/alarms`
- 大部分查询接口支持 `limit`/`offset` 分页参数，便于前端增量拉取数据。

## Tkinter GUI

FastAPI 服务运行时，可启动本地图形界面：
```bash
source venv_widows/bin/activate
python -m app.gui.app
```
GUI 以多标签页展示传感器、BMS、RFID 数据，并提供命令发送、历史记录与日志视图。
- “Images” 标签页会从 `/api/images` 拉取并展示最新行人截图，可直接在界面内预览。




## 一键运行（FastAPI + GUI）
根目录提供 `run_all.py` 脚本，自动：
- 创建/更新 `venv_widows` 虚拟环境
- 安装 `requirements.txt` 中的依赖
- 同时启动 FastAPI（uvicorn）与 Tkinter GUI（`python -m app.gui.app`）

在 Windows 或 Linux 中运行：
```bash
python run_all.py
```
`Ctrl+C` 可停止所有子进程。

如需仅启动服务端（不拉起 GUI），在 `.env` 中设置：
```
GUI_ENABLED=false
```

## 启动流程工作原理

本项目常用两种启动方式：
1. 一键启动（推荐）：`python run_all.py`
2. 手动启动：先启动 FastAPI（uvicorn），再按需启动 GUI

### 1) `run_all.py`（一键启动）做了什么
- 读取 `.env`（`python_dotenv.load_dotenv()`），用于控制安装依赖/启动 GUI/日志级别等行为。
- 检测 `venv_widows` 是否可用：如果 venv 被移动导致不可执行，会先尝试 `python -m venv --upgrade venv_widows` 修复；仍失败则重建 venv。
- 按环境变量决定是否安装依赖：
  - `SKIP_PIP_INSTALL=true` 或 `INSTALL_DEPENDENCIES=false` 会跳过安装。
  - 否则会执行 `python -m pip install -r requirements.txt`。
- 启动 FastAPI（uvicorn）：`python -m uvicorn main:app --host ... --port ...`，并做短暂的“存活检查”，避免服务立即退出但无提示。
- 若 `GUI_ENABLED=true`，再启动 Tkinter GUI（`python -m app.gui.app`）；否则仅运行 FastAPI。
- `Ctrl+C` 退出时会向所有子进程发送 `terminate`，必要时 `kill`，保证一键停止。

常用启动相关环境变量：
- `GUI_ENABLED`：是否启动 GUI（默认 `true`）。
- `UVICORN_LOG_LEVEL`：uvicorn 日志级别（如 `info/warning/error`）。
- `SKIP_PIP_INSTALL` / `INSTALL_DEPENDENCIES`：是否安装依赖。

### 2) FastAPI 启动后内部初始化顺序
FastAPI 的入口是 `main.py`（`app = create_app()`）。`create_app()`（`app/api/__init__.py`）会按以下顺序初始化：
1. 加载配置：`load_env()` 读取 `.env`，`get_settings()` 生成全局 `Settings`。
2. 创建 MQTT：初始化 `MQTTManager` 并挂到 `app.state.mqtt`，随后注册各主题处理器（传感器/BMS/RFID/命令回执）。
3. 创建后台服务：创建 `YOLOStreamService`、`AudioMonitorService`（共享同一个 `mqtt_manager`），挂到 `app.state.yolo` / `app.state.audio`。
4. 创建运行时监督器：创建 `RuntimeSupervisor`（`app/services/runtime_supervisor.py`），挂到 `app.state.supervisor`。
5. 注册生命周期事件：
   - `startup`：`supervisor.start()`（开始后台 ensure/重试）。
   - `shutdown`：依次停止 `supervisor`、YOLO、Audio，并断开 MQTT。
6. 挂载 API 路由：`application.include_router(api_router)`。

### 3) RuntimeSupervisor：为什么 DB/MQTT 没就绪也能自动恢复
生产环境常见情况是 MySQL 或 MQTT Broker 启动慢于 API：如果启动阶段直接失败退出，只能靠外部反复重启。为提高健壮性，项目引入了 `RuntimeSupervisor` 做后台 ensure：
- **MQTT ensure**：循环调用 `mqtt_manager.connect()`；MQTT 使用 `connect_async + loop_forever(retry_first_connection=True)`，Broker 未 ready 时会持续重试，避免“首次连接失败后永不恢复”。
- **DB 初始化重试**：循环调用 `init_db()`；MySQL 未 ready 时会指数退避重试（上限可配置），直到成功创建/修复表结构。
- **服务延迟启动**：只有当 `init_db()` 成功后，才会尝试启动 YOLO/音频后台线程（各服务仍会根据 `YOLO_ENABLED/AUDIO_ENABLED` 自行决定是否真正运行）。
- **状态上报**：supervisor 会维护 `db_ok/mqtt_ok/db_initialized/services_started/last_db_error` 等状态供健康检查输出。

### 4) 健康检查（liveness vs readiness）
为了便于 watchdog/守护进程判定是否需要重启，健康检查被拆分为：
- `GET /api/health/live`：**liveness**，只表示进程存活（通常永远返回 200）。
- `GET /api/health/ready`：**readiness**，只有当 DB 与 MQTT 都可用时返回 200，否则返回 503（用于判定“服务是否真正可用”）。
- `GET /api/health`：汇总输出，包含 `db_ok/mqtt_ok` 以及 supervisor 的更多诊断字段。

### 5) MQTT 入库与告警广播的串联逻辑
- MQTT 入库：收到 `sensors/data`、`sensors/bms`、`sensors/rfid` 等消息后会进入 `app/services/ingestion.py`，解析 JSON 载荷并写入数据库。
- 设备与位置填充：
  - **device_id**：对 MQTT 采集的数据，默认使用 **MQTT topic 全串**作为 `device_id`（如 `sensors/data/gateway01`）。
  - **location**：对传感器/BMS/音频/图像数据，若数据库存在最新 RFID 记录，则用其 `card_id` 覆盖 `location`。
- 告警：传感器阈值、BMS 低压、音频阈值、YOLO 行人检测、以及 API 手动创建告警，都会统一发布到 MQTT `sensors/alarms`，前端只需订阅一个 topic 即可接收所有告警。

### 新电脑迁移（尽量少安装）
- `venv_widows` 不能保证可直接拷贝复用（`pyvenv.cfg` 会记录旧电脑 Python 路径）。`run_all.py` 已支持自动检测并尝试 `python -m venv --upgrade` 修复 venv（尽量保留已安装的包）。
- 建议只安装 2 个前置：Python 3.11（尽量与原版本一致）+ FFmpeg（提供 `ffmpeg/ffplay`，并加入 PATH）。
- Windows 推荐一键启动：双击 `start_windows.bat`（或 PowerShell 执行 `.\start_windows.ps1`）。
- 如果需要重新装依赖：将 `.env` 中 `SKIP_PIP_INSTALL=false`、`INSTALL_DEPENDENCIES=true` 后再启动一次即可自动安装。

## MQTT 数据
- `app/mqtt.MQTTManager` 提供连接管理、主题订阅与消息发布。
- 默认订阅 `sensors/data`、`sensors/bms`、`sensors/rfid`、`sensors/command/response`。
- 统一告警广播主题：`sensors/alarms`（各类告警会以 JSON 事件的形式发布到该 topic，便于前端统一订阅）。
- 对传感器/BMS/音频/图像数据：入库时若存在最新 RFID 记录，会优先用其 `card_id` 覆盖 `location`。
- `app/services/ingestion.py` 负责解析上述主题的 JSON 载荷并写入数据库，同时记录命令回执。
- `POST /api/commands` 将十六进制指令发布到 `sensors/command/request` 供现场设备执行。

`sensors/alarms` 事件结构（schema v1，示例）：
```json
{
  "schema": 1,
  "event": "alarm",
  "source": "sensor_threshold",
  "timestamp": "2025-12-16T10:00:00.000000",
  "device_id": "sensors/data/gateway01",
  "location": "CARD123",
  "payload": {}
}
```
当前已发布的 `source`：`sensor_threshold`、`bms_cell_voltage`、`audio_threshold`、`yolo_person`、`api_alarm`。

## YOLO RTSP 行人识别
- `YOLOStreamService` 同步 `yolo.py` 的逻辑：使用 YOLOv8 `track()`（默认 ByteTrack，可切 Botsort）在 GPU/CPU 自动选设备，检测到行人时同时把原始与标注 JPEG 推到 `yolo/person_img`、`yolo/annotated_img`，并把原始图存入 `image_data` 表。
- 支持 FFmpeg 重推 RTSP，需在 `.env` 配置目标地址；未配置则只做检测+MQTT/入库。
- `.env` 示例：
  ```
  YOLO_ENABLED=true
  YOLO_RTSP_INPUT=rtsp://admin:password@192.168.0.64:554/cam/realmonitor?channel=1&stream=0
  YOLO_RTSP_OUTPUT=rtsp://127.0.0.1:8554/yolo
  YOLO_MODEL_PATH=yolov8n.pt
  YOLO_DEVICE_ID=camera-01
  YOLO_LOCATION=gate-a
  YOLO_DETECTION_INTERVAL=5
  YOLO_PERSON_CONFIDENCE=0.5
  YOLO_TRACKER_CONFIG=bytetrack.yaml
  YOLO_FRAME_WIDTH=960
  YOLO_FRAME_HEIGHT=540
  YOLO_FPS=25
  YOLO_FFMPEG_ENABLED=true
  ```
- 依赖 `opencv-python`、`ultralytics` 已写入 `requirements.txt`；使用 GPU 时需安装对应版本 `torch`。

## 音频监测
- `AudioMonitorService` 使用 `ffmpeg` 从 RTSP 拉取音频，并按 `AUDIO_WINDOW_SECONDS` 分段录制为 WAV（PCM16）。当窗口特征超过阈值时才会把该片段写入 `audio_data` 表（`audio_data.audio_data` 推荐 MySQL `LONGBLOB`，默认采用）。
- 当所有阈值都为 `0` 时，会写入所有窗口（相当于开启录音）。
- API/GUI 仍使用 Base64 进行 JSON 传输：`GET /api/audio` 返回 Base64 字符串；`POST /api/audio` 接收 Base64 并写入为 bytes。
- 阈值接口 `/api/audio/thresholds` 用于持久化阈值配置，并会实时影响服务端入库逻辑。
- `AUDIO_THRESHOLD_RMS` 支持两种尺度：`<=1.0` 视为归一化 RMS（0~1），`>1.0` 视为 int16 RMS（0~32767）。
- `.env` 示例：
  ```
  AUDIO_ENABLED=true
  AUDIO_RTSP_INPUT=rtsp://admin:password@192.168.0.64:554/cam/realmonitor?channel=1&stream=0
  AUDIO_DEVICE_ID=mic-01
  AUDIO_LOCATION=gate-a
  AUDIO_SAMPLE_RATE=16000
  AUDIO_WINDOW_SECONDS=1.0
  AUDIO_THRESHOLD_CENTROID=0
  AUDIO_THRESHOLD_BANDWIDTH=0
  AUDIO_THRESHOLD_ROLLOFF=0
  AUDIO_THRESHOLD_FLATNESS=0
  AUDIO_THRESHOLD_FLUX=0
  AUDIO_THRESHOLD_RMS=0
  ```
- 依赖 `ffmpeg/ffplay` 可执行文件；`numpy`、`librosa` 等依赖已写入 `requirements.txt`（GUI 音频特征可视化使用）。
## 开发规范
- 遵循 PEP8、类型标注及 Google 风格文档字符串；必要时使用 `black`
- 单文件代码不超过 500 行，按功能拆分模块
- 所有数据库写入通过 `app/services/data_service.py`，保证事务一致性与阈值判定
- 新增功能时同步更新 `TASK.md` 与本文档，并维护依赖
