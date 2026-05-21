# 仓库贡献指南

> 约定：上位机运行于 Windows（Conda）；下位机运行于 loongarch64 GNU/Linux MIPS64 R2 架构（Python 3.9）。

## 项目结构
- `server/`：上位机（FastAPI + SQLModel/MySQL + MQTT + Tkinter GUI）。
  - `server/app/api/`：API 路由与应用工厂
  - `server/app/services/`：入库、阈值、告警、运行时守护
  - `server/app/db/`：SQLModel 模型与会话/引擎
  - `server/app/mqtt/`：MQTT 下位机封装
  - `server/app/gui/`：Tkinter GUI
  - `server/tests/`：pytest 测试
- `client/`：下位机（RS485 轮询 + RFID + MQTT 上报）。
  - `client/communication/`：MQTT/串口/ModbusTCP
  - `client/sensors/`、`client/utils/`

## 构建、测试与开发命令
- 上位机一键启动（Conda 推荐）：
  - `server/scripts/conda_run.ps1 -EnvName "sensor_server" -Mode "stack"`
  - `server/scripts/conda_run.bat "sensor_server" "stack"`
- 兼容旧方式（venv，集中到 `server/legacy_venv/`）：
  - `server/start_windows.bat` / `server/start_windows.ps1`
  - `py -3.11 "server/run_all.py"`
- 仅启动 API：
  - `conda run -n sensor_server python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000`
- 运行上位机测试（Conda 环境）：
  - `conda run -n sensor_server python -m pytest`
- 运行下位机（系统 Python 3.9）：
  - `cd "client" && "python3.9" "main.py"`

## 代码风格与命名
- Python 4 空格缩进；新增/修改代码保持 PEP 8 与类型标注。
- 命名：`snake_case.py`、`snake_case` 函数/变量、`PascalCase` 类、`UPPER_SNAKE_CASE` 常量。
- 模块边界：HTTP 在 `server/app/api/`，持久化在 `server/app/db/`，领域逻辑在 `server/app/services/`。

## 测试指南
- 使用 pytest（`server/tests/test_*.py`）。
- 优先单元测试，不依赖真实 MySQL/MQTT/RTSP。

## 文档维护
- 重要变更需同步更新：`server/README.md`、`client/README.md`、`docs/architecture.md`。
- PLC 相关测试更新：`PLC_TEST_GUIDE.md`。

## 安全与配置
- `server/.env` 控制运行行为（GUI、MQTT、MySQL 等）。不要提交生产密钥。
- 大文件/生成物（`*/venv_*`、`__pycache__/`、`.pytest_cache/` 等）不要提交。

## PR 规范
- 使用 Conventional Commits（`feat:`/`fix:`/`chore:`）。
- PR 至少包含：问题描述、已运行的测试命令、配置变更，以及 GUI 变更截图（如适用）。

## Codex 检索笔记

> 检索约定：本节是现场索引，不是完整设计文档。后续 Codex 应优先用 `rg "IDX-*|关键词" AGENTS.md server/app client ...` 定位相关小节，再按需读取对应文件；不要把整个 `AGENTS.md` 当作长期上下文塞入。

### IDX-SERVER-RUNTIME 运行环境与进程
- 关键词：`Windows`、`Conda`、`sensor_server`、`uvicorn main:app`、`port 8000`。
- 上位机 API 常用实际启动命令：`C:\Users\lpb\anaconda3\envs\sensor_server\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000`，工作目录为 `server/`。
- 常用健康检查：`GET http://127.0.0.1:8000/api/health`。健康响应里重点看 `db_ok`、`mqtt_ok`、`db_initialized`、`services_started`、`read_db_worker_queue_size`、`write_db_worker_queue_size`。
- 本地调试日志常用文件：`server/logs/api_live_check.out.log`、`server/logs/api_live_check.err.log`。
- 启动后可能由 API 拉起子进程：`app.services.yolo_worker`、`app.services.audio_worker`、`go2rtc.exe`。

### IDX-SERVER-MQTT-FLOW MQTT 主题与数据流
- 关键词：`MQTT_TOPICS`、`sensors/data`、`sensors/bms`、`sensors/rfid`、`handle_sensor_payload`。
- 主题常量在 `server/app/core/constants.py`。传感器主题为 `sensors/data`，BMS 为 `sensors/bms`，RFID 为 `sensors/rfid`。
- MQTT 客户端封装在 `server/app/mqtt/client.py`，worker 在 `server/app/runtime/mqtt_worker.py`。应用工厂 `server/app/api/__init__.py` 注册 MQTT handler。
- 下位机 `client/main.py` 的 `read_sensors_loop` 会把多个单项传感器读数组成列表发布到 `sensors/data`，每个 item 常见字段为 `sensor_type`、`value`、`timestamp`、`ts`、`schema`。

### IDX-SERVER-MQTT-INGEST MQTT 入库与传感器快照
- 关键词：`MQTT 入库`、`sensor snapshot`、`backfill`、`上一次数据`、`get_latest_sensor_data`。
- 传感器 MQTT 入库入口：`server/app/services/ingestion.py::handle_sensor_payload`。
- 传感器解析支持列表批量上报；不要假设一条 MQTT 消息一次包含全部 8 个字段。
- 新规则：写入 `sensor_data` 前，如果本次消息缺少某些传感器项，使用同 `device_id`、同 `location` 的上一条已写入记录补齐缺失值。
- 上一条记录查询在 `server/app/services/data_service.py::get_latest_sensor_data`，按数据库插入顺序 `id desc` 判断“上一次”，避免现场 payload 时间乱序导致回填错误。
- 入库时 `handle_sensor_payload` 会把 `record.device_id` 设为 MQTT topic（通常是 `sensors/data`）；如果存在最新 RFID 卡号，会把 `location` 覆盖为最新 `card_id`，所以查询时不要只按 payload location 判断。
- 已有历史碎片记录不会自动修复；修复生效后只影响后续新入库数据。如需修复历史数据，应写一次性脚本按时间或 id 顺序回填。
- 相关测试：`server/tests/test_mqtt_ingestion.py`，覆盖部分字段入库和缺失字段从上一条记录回填。

### IDX-SERVER-DB-WORKER DB Worker 与 API 读写边界
- 关键词：`DBWorker`、`read_db_worker`、`write_db_worker`、`RoutedAsyncDataServiceProxy`。
- DB worker 实现在 `server/app/runtime/db_worker.py`。应用启动时在 `server/app/api/__init__.py` 创建 `read_db_worker`、`write_db_worker`、`media_db_worker`。
- API 层通过 `server/app/api/routes.py::get_data_service` 使用 `RoutedAsyncDataServiceProxy`，按方法名前缀把写操作路由到 write worker，读操作路由到 read worker。
- 架构测试 `server/tests/test_architecture_guards.py` 限制直接打开 DB session 的位置；不要在普通 service 或 route 中随意创建 `Session`。

### IDX-SERVER-GUI-REFRESH GUI 聚合刷新
- 关键词：`GUI refresh`、`/api/gui/refresh`、`sections timeout`、`模块刷新失败`。
- GUI 聚合接口在 `server/app/api/routes.py::gui_refresh`。
- 已知问题：把多个 section 并发提交到单线程 `read_db_worker` 会造成队列排队，表现为 HTTP 200 但 `sensors/bms/rfid/... timeout`。当前实现应使用一次 DB 批量读取，避免 section 之间互相排队超时。
- GUI 客户端刷新逻辑在 `server/app/gui/app.py`，`_refresh_data_via_aggregate` 调 `/api/gui/refresh`，`_convert_gui_refresh_payload` 会把 section 的 `ok=false` 记录为“模块刷新失败”。
- 相关测试：`server/tests/test_gui_refresh_api.py`、`server/tests/test_gui_pagination_payloads.py`。

### IDX-SERVER-MEDIA-LIST 媒体列表性能
- 关键词：`images timeout`、`audio timeout`、`include_data=False`、`load_only`、`media lightweight`。
- 媒体列表接口在 `server/app/api/routes.py`：`GET /api/images`、`GET /api/audio`。详情接口为 `/api/images/{id}/data`、`/api/audio/{id}/data`。
- 列表查询默认不应读取大字段 `image_data`、`audio_data`；只在详情接口读取完整数据。
- `server/app/services/data_service.py::list_image_data` 和 `list_audio_data` 支持 `include_data=False`，应使用轻量字段查询，避免 SQL 扫描大 payload 列导致 GUI 刷新超时。
- 相关测试：`server/tests/test_media_query_timeout.py`、`server/tests/test_media_lightweight_payloads.py`。

### IDX-SERVER-TROUBLESHOOTING 常见现场现象与判断
- 关键词：`收到 MQTT 但没入库`：优先查 `handle_sensor_payload` 解析、`DBWorker` 是否启动、`db_ok/mqtt_ok`、以及消息是否为逐传感器列表格式。
- 关键词：`传感器行只有一个字段`：检查缺失字段回填逻辑是否生效，确认服务已重启到当前代码，并查询新写入记录而不是旧历史碎片。
- 关键词：`location 变成 00`：通常是最新 RFID 卡号覆盖位置，不一定是传感器 payload 的 location 丢失。
- 关键词：`HTTP 200 但 GUI 模块 timeout`：检查 `/api/gui/refresh` section payload，通常是 DB worker 排队或媒体列表读取大字段。
- 关键词：`RTSP/FFmpeg/GStreamer`：日志里的 `DESCRIBE failed`、`Unsupported transport`、`GStreamer bindings unavailable` 多数属于视频源或媒体栈问题，不等同于 MQTT/DB 入库失败。

### IDX-SERVER-VALIDATION 推荐验证命令
- 快速测试上位机：`cd server; python -m pytest tests\test_mqtt_ingestion.py tests\test_gui_refresh_api.py -q`。
- 媒体和 GUI 性能测试：`cd server; python -m pytest tests\test_media_query_timeout.py tests\test_media_lightweight_payloads.py tests\test_gui_refresh_api.py -q`。
- 架构守卫：`cd server; python -m pytest tests\test_architecture_guards.py -q`。
- 在线健康检查：`Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/api/health | Select-Object -ExpandProperty Content`。
- 最新传感器查询：`Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:8000/api/sensors/latest?limit=10" | Select-Object -ExpandProperty Content`。

### IDX-SERVER-API-STABILITY 北极星：API 稳定性 / 媒体链路隔离
- 关键词：`北极星`、`API稳定性`、`媒体链路隔离`、`控制面`、`数据查询面`、`媒体面`、`8888`、`8000`、`8554`、`8555`、`MEDIA_STORAGE_MODE`、`MEDIA_WRITE_QUEUE_CAPACITY`、`MEDIA_READ_QUERY_TIMEOUT_SECONDS`。
- 最高优先级：`server` 的 API 服务不能掉线、不能卡顿；摄像头掉线、RTSP 卡顿、YOLO 推流失败、音频解析失败、媒体写入高峰，都不能拖死 `/api/*`。
- 控制面/数据查询面：浏览器只访问 `http://192.168.0.100:8888/index`；nginx `:8888` 反代 API 到 `127.0.0.1:8000`；`FastAPI :8000` 必须稳定。
- 媒体面：`8554/8555` 允许降级、重试、离线，但不能影响 `8888/8000`。页面最多显示“媒体离线/数据延迟”，不能因为媒体故障变成“服务异常无数据”。
- 用户约束：除非用户明确要求，不要改动前端页面；优先在后端 API、worker、存储和超时边界处理降级。

### IDX-SERVER-PORTS-RTSP 端口规划 / RTSP 拓扑
- 关键词：`端口规划`、`go2rtc 独占 8554`、`YOLO 输出 8555`、`nodejs-ws 8089 8090`、`MQTT 1883`。
- `8888`：前端入口，浏览器只访问 `http://192.168.0.100:8888`。
- `8000`：FastAPI 内部服务，nginx 反代到 `127.0.0.1:8000`。
- `8554`：摄像头统一输入，只给 `go2rtc` 使用；不要让 `yolo_worker` 或其他服务监听/接管此端口。
- `8555`：YOLO 输出 RTSP，例如 `rtsp://192.168.0.100:8555/yolo`；不要占用 `8554`。
- `8089/8090`：`nodejs-ws` 独立运行，不和 API 抢端口。
- `1883`：MQTT 独立运行。

### IDX-SERVER-FAILURE-MODES 已实锤故障模式
- 关键词：`uvicorn 没死`、`业务接口超时`、`/api/health/live`、`404 Not Found`、`数据库大对象`、`页面无数据`。
- `:8000` 端口仍监听，但多个业务接口 6 秒内无响应，说明 uvicorn 进程未死，实际业务链路被卡住。
- `/api/health/live` 能快速返回，说明轻量健康接口正常；卡住的是数据库查询、媒体查询、worker 共享资源或同步阻塞链路。
- `:8554` 曾被 `go2rtc.exe` 和 `yolo_worker` 争用，导致 `rtsp://127.0.0.1:8554/cam01` 被错误服务接管并出现 `404 Not Found`。
- `MEDIA_STORAGE_MODE=database` 会把图片/音频等大对象写入数据库；媒体写入高峰可能影响 API 查询。
- 前端刷新会并发调用多个接口；后端必须保证 `/api/images`、`/api/audio` 等失败或超时时返回降级结果，而不是拖死整页。

### IDX-SERVER-ENV-STOPGAP 短期止血环境配置
- 关键词：`短期止血`、`.env`、`filesystem`、`AUDIO_STORE_MIN_INTERVAL_SECONDS`、`YOLO_FPS`、`YOLO_INFERENCE_INTERVAL`、`YOLO_INFERENCE_SIZE`。
- 推荐 `.env` 方向：
  ```dotenv
  MEDIA_GATEWAY_ENABLED=true
  MEDIA_GATEWAY_RELAY_RTSP=rtsp://127.0.0.1:8554/cam01
  MEDIA_GATEWAY_CONTROL_PANEL_RTSP=rtsp://127.0.0.1:8554/cam01

  YOLO_RTSP_INPUT=rtsp://127.0.0.1:8554/cam01
  AUDIO_RTSP_INPUT=rtsp://127.0.0.1:8554/cam01
  YOLO_RTSP_OUTPUT=rtsp://192.168.0.100:8555/yolo

  MEDIA_STORAGE_MODE=filesystem
  MEDIA_WRITE_QUEUE_CAPACITY=1000
  MEDIA_READ_QUERY_TIMEOUT_SECONDS=2.0
  AUDIO_STORE_MIN_INTERVAL_SECONDS=30
  YOLO_FPS=10
  YOLO_INFERENCE_INTERVAL=0.5
  YOLO_INFERENCE_SIZE=512
  ```
- 关键止血点：确保 `go2rtc` 独占 `8554`；YOLO 输出改到 `8555`；媒体大对象走文件系统，数据库只保存轻量元数据和文件路径。

### IDX-SERVER-BACKEND-ISOLATION 后端隔离实现约束
- 关键词：`媒体写入队列`、`满了丢弃旧图片`、`满了丢弃旧音频`、`查询超时返回空列表`、`不阻塞 API`、`lazy import`。
- 媒体写入队列必须有上限；队列满时丢弃旧图片/旧音频，不能阻塞 API 请求线程或事件循环。
- `/api/images`、`/api/audio` 查询必须有短超时；超时返回空列表和状态字段，不能让整页刷新等待到失败。
- RTSP、ffmpeg、OpenCV、YOLO、音频解析只能影响 worker；不要在 API 请求路径里做阻塞式媒体打开、推理、音频解码或大文件写入。
- 大对象不入库：图片/音频落盘到 `MEDIA_STORAGE_DIR`，数据库只保存文件路径、时间、设备、阈值、事件类型等轻量元数据。
- 可选依赖要延迟导入或失败降级：缺少 `cv2`、`gi`、`librosa`、CUDA 不可用时，`import main` 和轻量 API 仍应成功。

### IDX-SERVER-MEDIA-STATUS-API 运行时状态接口目标
- 关键词：`/api/runtime/media-status`、`camera_online`、`go2rtc_online`、`yolo_online`、`audio_online`、`last_frame_at`。
- 中期目标：新增媒体状态接口，返回 `camera_online`、`go2rtc_online`、`yolo_online`、`audio_online`、`last_frame_at`。
- 状态接口只读轻量状态缓存，不主动打开 RTSP，不运行 ffmpeg，不查询大对象。
- 需要增加 API 请求耗时日志：记录超过 1 秒的接口、SQL、DB worker 队列长度。

### IDX-SERVER-IMPORT-FIXES 已补齐的启动导入模块
- 关键词：`ModuleNotFoundError`、`media_storage`、`audio_monitor`、`config_options`、`rtsp_capture`、`audio_metrics`、`latest_cache`。
- `server/main.py`：ASGI 入口，导出 `app = create_app()`，用于 `uvicorn main:app`。
- `server/app/services/media_storage.py`：提供 `store_image_base64`、`store_audio_bytes`、`load_file_base64`，支持文件系统媒体存储。
- `server/app/gui/audio_monitor.py`：提供 `AudioRTSPMonitor`，避免 GUI 导入失败。
- `server/app/gui/config_options.py`：Tkinter GUI 的运行时配置兜底清单；API 仍以 `/api/runtime-config/options` 为权威。
- `server/app/services/audio_metrics.py`：音频指标计算，`librosa/numpy` 延迟导入，失败返回空指标以保持 API 可导入。
- `server/app/services/rtsp_capture.py`：RTSP capture 工具，`cv2` 延迟导入，缺依赖时降级。
- `server/app/services/latest_cache.py`、`sensor_cache.py`、`bms_cache.py`、`rfid_cache.py`：轻量最新数据缓存，供 API/ingestion 解耦。

### IDX-SERVER-IMPORT-VALIDATION 启动导入验收命令
- 关键词：`main_import_ok`、`conda_import_ok`、`routes_import_ok`、`gui_import_chain_ok`。
- 在 `server/` 下验证：
  ```powershell
  python -c "import main; print('main_import_ok')"
  python -c "from app.api.routes import router; print('routes_import_ok', len(router.routes))"
  conda run -n sensor_server python -c "from app.gui.app import AudioRTSPMonitor; import main; print('conda_import_ok')"
  ```
- 注意：基础 Python 可能缺 `numpy/librosa/PIL`，GUI 相关验证优先使用 Conda 环境 `sensor_server`。

### IDX-SERVER-API-VALIDATION API 稳定性验收命令
- 关键词：`验收测试`、`Invoke-WebRequest`、`ffmpeg`、`health live`、`runtime-config`、`sensors latest`。
- 每次调整后执行：
  ```powershell
  .\scripts\stop_all.bat
  .\scripts\start_all.bat

  Invoke-WebRequest http://127.0.0.1:8000/api/health/live -UseBasicParsing
  Invoke-WebRequest http://127.0.0.1:8000/api/runtime-config -UseBasicParsing -TimeoutSec 5
  Invoke-WebRequest "http://192.168.0.100:8888/api/sensors/latest?limit=5" -UseBasicParsing -TimeoutSec 5

  ffmpeg -rtsp_transport tcp -i rtsp://127.0.0.1:8554/cam01 -t 1 -f null -
  ffmpeg -rtsp_transport tcp -i rtsp://192.168.0.100:8555/yolo -t 1 -f null -
  ```
- 目标指标：`/api/health/live < 100 ms`、`/api/runtime-config < 300 ms`、`/api/sensors/latest?limit=5 < 500 ms`、页面首轮数据刷新 `< 2 s`。

### IDX-SERVER-RTSP-GATEWAY RTSP / 媒体入口关键词
- 关键词：`RTSP`、`go2rtc`、`MediaMTX`、`媒体接入层`、`统一入口`、`拉流抢占`、`control-panel`、`nodejs-ws`、`YOLO_RTSP_INPUT`、`AUDIO_RTSP_INPUT`、`MEDIA_GATEWAY_*`。
- 已识别的 RTSP 消费方：YOLO 视频流、音频检测、视频流中转服务器、浏览器 `http://127.0.0.1:8888/control-panel`。
- 用户目标：最终应通过媒体接入层统一拉相机源，再由各消费方读本机代理地址，避免多个进程同时抢占海康设备 RTSP 会话。
- 当前阶段：`server` 侧已有媒体网关配置骨架；`nodejs-ws/index.js` 和 control-panel 的 `demo.js` 已按用户要求回退，不再强制改为统一入口。

### IDX-SERVER-MEDIA-GATEWAY-CONFIG server 侧媒体网关配置
- 主要文件：`server/app/core/config.py`。
- 相关配置：`MEDIA_GATEWAY_ENABLED`、`MEDIA_GATEWAY_TYPE`、`MEDIA_GATEWAY_RELAY_RTSP`、`MEDIA_GATEWAY_CONTROL_PANEL_RTSP`、`MEDIA_GATEWAY_HTTP_API`、`MEDIA_GATEWAY_REWRITE_YOLO_INPUT`、`MEDIA_GATEWAY_REWRITE_AUDIO_INPUT`。
- 默认 API：`MEDIA_GATEWAY_HTTP_API=http://127.0.0.1:1984`，对应 go2rtc 常见管理接口；用户浏览器曾打开 `http://127.0.0.1:1984/api/streams`。
- 有效输入解析：`resolve_yolo_rtsp_input()`、`resolve_audio_rtsp_input()`、`resolve_control_panel_rtsp_input()` 会在启用媒体网关后优先使用代理入口。
- 相关测试：`server/tests/test_media_gateway_settings.py`、`server/tests/test_media_isolation_workers.py`、`server/tests/test_media_query_timeout.py`、`server/tests/test_rtsp_output.py`。

### IDX-EXTERNAL-NODEJS-WS 外部视频中转服务器 nodejs-ws
- 外部路径：`C:\Users\lpb\Desktop\nodejs-ws\index.js`。
- 当前状态：已回退为直连相机 RTSP，不读取 `RTSP_UNIFIED_BASE`、`RTSP_URL_CAMERA1`、`RTSP_URL_CAMERA2`。
- 当前两路源：`rtsp://admin:HIKKBA12@192.168.0.101:554/h264/ch1/main/av_stream`、`rtsp://admin:HIKKBA12@192.168.0.101:554/h264/ch2/main/av_stream`。
- 注意：`C:\Users\lpb\Desktop\nodejs-ws\start.ps1` 仍可能设置 `RTSP_UNIFIED_BASE=rtsp://127.0.0.1:8554`、`RTSP_URL_CAMERA1=.../cam01`、`RTSP_URL_CAMERA2=.../cam02`，但当前 `index.js` 不使用这些环境变量。

### IDX-EXTERNAL-CONTROL-PANEL control-panel / 海康 demo.js
- 外部路径：`C:\Users\lpb\Desktop\coil-design-v2.1.1\html\static\hikjs\demo.js`。
- 当前状态：用户已要求回退对 `demo.js` 的修改；不要默认再次改它。
- 差异点：control-panel 主要通过海康 WebVideoCtrl 插件走设备 Web 登录和预览流程，默认设备端口是 `80`，不是普通后端 ffmpeg 直接打开 `rtsp://...:554/...` 的方式。
- 已观察日志：`192.168.0.101_80 获取端口成功`、零通道/数字通道 403、模拟通道成功、自动登录成功。这说明 80 端口登录链路可用，但通道能力查询可能受权限或设备能力限制。

### IDX-CAMERA-CHANNELS 相机与通道约定
- 设备：`192.168.0.101`，RTSP 端口 `554`，Web/control 端口 `80`。
- 第一路常用源：`rtsp://admin:HIKKBA12@192.168.0.101:554/h264/ch1/main/av_stream`。
- 第二路用户确认源：`rtsp://admin:HIKKBA12@192.168.0.101:554/h264/ch2/main/av_stream`。
- 曾讨论映射：统一入口示例为 `rtsp://127.0.0.1:8554/cam01`、`rtsp://127.0.0.1:8554/cam02`。不要把两路都映射到同一个 `cam01`。

### IDX-SERVER-CHANGE-RULES 后续改动原则
- 若继续推进统一入口，优先在 go2rtc/MediaMTX 中建立 `cam01`、`cam02` 两个独立源，再让 YOLO、音频检测、nodejs-ws 读取代理 RTSP。
- control-panel 的统一入口需要单独处理，因为它依赖 80 端口登录/WebVideoCtrl 流程；除非用户明确要求，不要直接替换 `demo.js` 的登录和预览逻辑。
- 涉及外部路径 `nodejs-ws` 或 `coil-design-v2.1.1` 时，先检查当前文件状态；这些目录不属于本仓库，可能已被用户手动改动。

## Client 现场检索索引（板端）

> 检索约定：本节用于 `client` 下位机运维与稳定性调试。后续 Codex 应先 `rg "IDX-CLIENT-*|关键词"` 命中目标小节，再按小节里的文件/命令执行；不要把整个 `AGENTS.md` 长期塞入上下文。

### IDX-CLIENT-BOARD-BASE
- 关键词：`192.168.0.102`、`/opt/loonginx-client`、`python3.9`、`run_client_forever.sh`。
- 开发板地址：`192.168.0.102`（会话中持续使用）。
- 板端工作目录：`/opt/loonginx-client`。
- client 主进程常见命令：`/opt/python3.9/bin/python3.9 -u /opt/loonginx-client/main.py`。
- 常用重启方式：`nohup /opt/loonginx-client/run_client_forever.sh </dev/null >/opt/loonginx-client/logs/launcher.log 2>&1 &`。

### IDX-CLIENT-SERIAL-MAP
- 关键词：`ttyS6`、`ttyS5`、`BMS`、`RFID`。
- 端口约定：
  - `/dev/ttyS6`：RS485 总线，含 `BMS` 与多数传感器轮询。
  - `/dev/ttyS5`：RFID 监听链路（历史监听数据来自此口）。

### IDX-CLIENT-TTYS6-RELEASE
- 关键词：`release_ttys6.py`、`占用进程`、`kill ttyS6`。
- 仓库脚本：`client/scripts/release_ttys6.py`。
- 板端脚本：`/opt/loonginx-client/scripts/release_ttys6.py`。
- 用途：结束所有占用 `/dev/ttyS6` 的进程并回显结果。
- 运行命令：`python3.9 /opt/loonginx-client/scripts/release_ttys6.py`。
- 扩展参数：`--device /dev/ttyS5` 可释放其他串口。

### IDX-CLIENT-BMS-ONE-SHOT
- 关键词：`probe_bms_after_release.py`、`释放后探测`、`BMS 单次探测`。
- 仓库脚本：`client/scripts/probe_bms_after_release.py`。
- 板端脚本：`/opt/loonginx-client/scripts/probe_bms_after_release.py`。
- 行为：先释放 `ttyS6`，再执行一次 BMS 读取并输出 JSON。
- 运行命令：`python3.9 /opt/loonginx-client/scripts/probe_bms_after_release.py`。

### IDX-CLIENT-BMS-UNTIL-COMPLETE
- 关键词：`probe_bms_until_complete.py`、`完整字段`、`循环探测`。
- 板端脚本：`/opt/loonginx-client/scripts/probe_bms_until_complete.py`。
- 行为：循环探测 BMS，直到字段完整或超出上限（用于“全部字段返回才停”类需求）。

### IDX-CLIENT-RUNTIME-LOGS
- 关键词：`sensor_gateway.log`、`launcher.log`、`Serial lock timeout`、`Sensors skipped in batch`。
- 板端常看日志：
  - `/opt/loonginx-client/sensor_gateway.log`
  - `/opt/loonginx-client/logs/launcher.log`
- 关键判据：
  - 出现 `Serial lock timeout operation=read_registers_direct port=/dev/ttyS6`：通常表示串口事务阻塞或设备响应拖长。
  - 出现 `Sensors skipped in batch: ...`：本轮存在缺项，需结合缓存/降级策略判断是否可接受。

### IDX-CLIENT-STABILITY-STRATEGY
- 关键词：`完整快照缓冲`、`降级版`、`photoelectric 优先级`、`30s~60s 完整上报`。
- 当前策略（会话落地）：
  - `photoelectric` 最高优先级，目标轮询延时不超过 `5s`。
  - `env`（地址 `15`）按三次独立读取：温度/湿度/烟雾分开读。
  - 非光电项采用“完整快照优先 + 超时降级”：
    - 优先攒齐完整字段再发完整版。
    - 超过窗口仍不齐时发降级版（带 `stale`），避免完全静默。

### IDX-CLIENT-KNOWN-BOTTLENECKS
- 关键词：`address 25`、`address 15`、`No response`、`IO output`、`photoelectric`。
- 已观察瓶颈：
  - `address=25`（光电/IO）易出现 `No response`，会拖慢 `ttyS6`。
  - `address=15`（环境）存在间歇失败，导致温湿烟完整率下降。
- 已采取减干扰项：
  - 启动阶段 IO 输出默认关闭（日志关键字：`Startup IO outputs disabled by config`）。
  - 相机灯输出默认关闭（日志关键字：`Camera light output disabled by config`）。

### IDX-CLIENT-VALIDATION-CMDS
- 关键词：`ps`、`pkill`、`pytest`、`health check`。
- 进程检查：`ps -ef | grep -E 'main.py|run_client_forever|python3.9 -u'`。
- 停止 client：`pkill -f '/opt/loonginx-client/main.py'`、`pkill -f '/opt/loonginx-client/run_client_forever.sh'`。
- 启动 client：`nohup /opt/loonginx-client/run_client_forever.sh </dev/null >/opt/loonginx-client/logs/launcher.log 2>&1 &`。
- 本地快速回归（Windows 仓库）：
  - `python -m pytest tests/test_output_queue.py tests/test_threshold_alignment.py tests/test_shangluo_environment_sensor.py tests/test_io_board.py -q`
