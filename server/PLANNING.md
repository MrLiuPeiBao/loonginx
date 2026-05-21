# 上位机规划与实现说明（与现状对齐）

> 目标：让普通人也能理解上位机职责，同时为工程师提供“逐文件/逐函数”的精确索引。

## 1. 目标与边界
- 目标：接收 MQTT 数据、入库、告警、提供 API/GUI、命令闭环。
- 边界：不直接采集硬件；采集由下位机完成。
- 兼容性：新增字段与功能不破坏旧版本协议。
- 稳定性：支持按时间/容量清理历史数据，避免数据库膨胀。

## 2. 运行流程（非专业视角）
- MQTT 订阅下位机数据 → 解析 → 入库 → 告警判断 → 发布告警。
- API/GUI 提供查询、参数配置、命令下发与回执跟踪。
- 守护线程负责 DB/MQTT 重连、命令超时处理与数据清理。

## 3. 配置策略（`server/.env`）
- MQTT：`MQTT_BROKER` / `MQTT_PORT` / `MQTT_USERNAME` / `MQTT_PASSWORD`
- MySQL：`MYSQL_HOST` / `MYSQL_PORT` / `MYSQL_USER` / `MYSQL_PASSWORD` / `MYSQL_DATABASE`
- API：`API_HOST` / `API_PORT`
- 命令闭环：`COMMAND_TIMEOUT_SECONDS`
- 数据保留：`DATA_RETENTION_DAYS` / `DATA_RETENTION_MAX_GB` / `DATA_RETENTION_CHECK_INTERVAL_SECONDS` / `DATA_RETENTION_TABLES`
- 媒体：`MEDIA_STORAGE_MODE` / `MEDIA_STORAGE_DIR` / `MEDIA_STORE_IMAGE` / `MEDIA_STORE_AUDIO`
- 视觉/音频：`YOLO_*` / `AUDIO_*`

## 4. 代码文件与函数说明（逐文件）

### 4.1 `server/main.py`
- `create_app()`：创建并返回 FastAPI 应用实例。

### 4.2 `server/app/api/__init__.py`
- `_bytes_to_hex()`：把二进制转成十六进制字符串，便于日志查看。
- `create_app()`：构建 FastAPI 应用，初始化 MQTT、服务对象并注册路由。

### 4.3 `server/app/api/routes.py`
- `get_data_service()`：API 依赖注入，获取 `DataService`。
- `get_audio_service()`：获取音频服务实例。
- `_is_probable_wav()`：判断字节是否可能为 WAV。
- `_audio_db_value_to_base64()`：音频数据库字段转 Base64。
- `_image_db_value_to_base64()`：图像数据库字段转 Base64。
- `_decode_audio_base64()`：解析上传音频 Base64。
- `_parse_datetime()`：解析时间字符串。
- `_parse_bool()`：解析布尔参数。
- `_parse_direction()`：解析命令方向过滤。
- `_parse_command_status()`：解析命令状态过滤。
- `_normalize_command_payload()`：统一命令 payload 格式。
- `_hex_to_bytes()`：十六进制字符串转字节。
- `health()`：综合健康检查。
- `health_live()`：存活探针。
- `health_ready()`：就绪探针。
- `list_sensor_data()`：查询传感器数据。
- `create_sensor_data()`：写入传感器数据。
- `latest_sensor_data()`：查询最新传感器数据。
- `list_bms_data()`：查询 BMS 数据。
- `create_bms_data()`：写入 BMS 数据。
- `list_rfid_data()`：查询 RFID 数据。
- `get_latest_rfid_data()`：查询最新 RFID。
- `create_rfid_data()`：写入 RFID 数据。
- `list_command_logs()`：查询命令日志。
- `send_command()`：下发串口命令。
- `list_command_requests()`：查询命令请求状态。
- `get_command_request()`：查询单条命令请求。
- `list_sensor_configs()`：查询传感器配置。
- `create_sensor_config()`：新增传感器配置。
- `update_sensor_config()`：更新传感器配置。
- `delete_sensor_config()`：删除传感器配置。
- `list_alarm_records()`：查询告警记录。
- `create_alarm_record_api()`：新增告警记录（API 入口）。
- `list_image_data()`：查询图像数据。
- `create_image_data()`：写入图像数据。
- `list_audio_data()`：查询音频数据。
- `create_audio_data()`：写入音频数据。
- `list_audio_metrics()`：查询音频指标。
- `update_audio_thresholds()`：更新音频阈值。
- `stream_audio_metrics()`：SSE 推送音频指标。
- `list_metal_anomaly()`：查询金属异物记录。
- `create_metal_anomaly()`：写入金属异物记录。

### 4.4 `server/app/core/config.py`
- `load_env()`：加载 `.env` 文件。
- `Settings.parse_cors_allow_origins()`：解析 CORS 白名单。
- `Settings.normalize_run_mode()`：规范化运行模式（thread/process）。
- `Settings.normalize_media_storage_mode()`：规范化媒体存储模式。
- `Settings.parse_data_retention_tables()`：解析保留表清单。
- `Settings.mysql_url`：生成数据库连接 URL。
- `get_settings()`：返回单例配置对象。

### 4.5 `server/app/core/constants.py`
- `MQTT_TOPICS`：MQTT 主题常量。

### 4.6 `server/app/db/models.py`
- `SensorTypeEnum`：传感器类型枚举。
- `SensorConfig`：传感器阈值配置表。
- `SensorData`：传感器数据表。
- `AlarmType`：告警类型枚举。
- `AlarmRecord`：告警记录表。
- `ImageData`：图像数据表。
- `AudioData`：音频数据表。
- `MetalAnomaly`：金属异物表。
- `BMSData`：电池 BMS 数据表。
- `RFIDData`：RFID 数据表。
- `CommandDirection`：命令方向枚举。
- `CommandLog`：命令日志表。
- `CommandStatus`：命令状态枚举。
- `CommandRequestState`：命令闭环状态表。

### 4.7 `server/app/db/audio_thresholds.py`
- `AudioThreshold`：音频阈值配置表。

### 4.8 `server/app/db/session.py`
- `init_db()`：初始化数据库并补齐缺失列。
- `db_ping()`：数据库健康检查。
- `get_session()`：获取数据库会话。
- `session_scope()`：上下文会话封装。
- `_ensure_alarm_table_schema()`：兼容修复告警表。
- `_ensure_image_table_schema()`：兼容修复图像表。
- `_ensure_audio_table_schema()`：兼容修复音频表。
- （已移除）`ingested_at` 自动补齐逻辑。
- `_ensure_alarm_location_column()`：补齐告警位置字段。
- `_ensure_alarm_type_enum()`：确保告警枚举完整。

### 4.9 `server/app/mqtt/client.py`
- `MQTTMessageContext.json()`：解析 JSON payload。
- `MQTTManager.__init__()`：初始化 MQTT 下位机。
- `MQTTManager.connect()`：连接 MQTT。
- `MQTTManager.disconnect()`：断开 MQTT。
- `MQTTManager.register_handler()`：注册主题处理器。
- `MQTTManager.unregister_handler()`：取消主题处理器。
- `MQTTManager.publish()`：发布消息。
- `MQTTManager.is_connected()`：连接状态判断。
- `MQTTManager._handle_connect()`：连接事件回调。
- `MQTTManager._handle_disconnect()`：断开事件回调。
- `MQTTManager._handle_message()`：消息分发。

### 4.10 `server/app/services/alarm_cache.py`
- `set_latest_alarm()`：写入最新告警缓存。
- `get_latest_alarm()`：读取最新告警缓存。

### 4.11 `server/app/services/alarm_publisher.py`
- `build_alarm_event()`：构建统一告警事件结构。
- `publish_alarm_event()`：发布告警事件到 MQTT。

### 4.12 `server/app/services/ingestion.py`
- `_publish_ingestion_failure()`：入库失败时生成降级告警。
- `_parse_datetime()`：解析时间。
- `_to_str()`：安全转字符串。
- `_safe_float()`：安全转浮点。
- `_decode_payload()`：解析 MQTT payload。
- `_resolve_device_id()`：补齐 device_id。
- `_resolve_location()`：补齐 location。
- `_build_sensor_record()`：构造传感器入库对象。
- `_extract_sensor_map()`：解析传感器值映射。
- `_parse_sensor_message()`：解析传感器消息。
- `_parse_bms_message()`：解析 BMS 消息。
- `_parse_rfid_message()`：解析 RFID 消息。
- `handle_sensor_payload()`：处理传感器数据入库。
- `handle_bms_payload()`：处理 BMS 数据入库。
- `handle_rfid_payload()`：处理 RFID 数据入库。

- `_parse_datetime()`：解析时间。
- `_to_str()`：安全转字符串。
- `_resolve_device_id()`：补齐 PLC 设备 ID。
- `_resolve_location()`：补齐位置。
- `_decode_payload()`：解析 PLC payload。

- `_to_bool_map()`：将状态值转布尔映射。
- `_extract_active_keys()`：提取触发的告警键。
- `_key_to_name()`：键名到中文描述。
- `_recently_raised()`：去抖判断。
- `extract_status_payload()`：抽取 PLC 状态摘要。

- `validate_control_command_code()`：校验控制指令编码。
- `validate_param_updates()`：校验参数更新范围。
- `FloatParamSpec`：浮点参数规格定义。

### 4.16 `server/app/services/data_service.py`
- `_resolve_retention_tables()`：解析数据保留表。
- `_estimate_db_size_gb()`：估算数据库大小。
- `DataService.__init__()`：构造数据服务。
- `DataService.consume_alarm_events()`：消费告警事件。
- `DataService.create_sensor_data()`：写入传感器数据。
- `DataService.list_sensor_data()`：查询传感器数据。
- `DataService.create_bms_data()`：写入 BMS 数据。
- `DataService.list_bms_data()`：查询 BMS 数据。
- `DataService.create_rfid_data()`：写入 RFID 数据。
- `DataService.list_rfid_data()`：查询 RFID 数据。
- `DataService.get_latest_rfid_card()`：查询最新 RFID 卡号。
- `DataService.list_sensor_configs()`：查询阈值配置。
- `DataService.upsert_sensor_config()`：新增或更新阈值配置。
- `DataService.delete_sensor_config()`：删除阈值配置。
- `DataService.list_alarm_records()`：查询告警记录。
- `DataService.create_alarm_record()`：写入告警记录。
- `DataService.add_command_log()`：写入命令日志。
- `DataService.list_command_logs()`：查询命令日志。
- `DataService.upsert_command_request()`：新增/更新命令请求。
- `DataService.update_command_request_status()`：更新命令状态。
- `DataService.list_command_requests()`：查询命令请求。
- `DataService.mark_command_timeouts()`：标记超时命令。
- `DataService.create_image_data()`：写入图像数据。
- `DataService.list_image_data()`：查询图像数据。
- `DataService.create_audio_data()`：写入音频数据。
- `DataService.list_audio_data()`：查询音频数据。
- `DataService.create_metal_anomaly()`：写入金属异物记录。
- `DataService.list_metal_anomaly()`：查询金属异物记录。
- `DataService._persist_entities()`：通用入库逻辑。
- `DataService._find_existing_sensor_record()`：查询同时间戳传感器记录。
- `DataService._merge_sensor_values()`：合并同时间戳传感器值。
- `DataService._upsert_sensor_record()`：插入或合并传感器记录。
- `DataService._evaluate_thresholds()`：阈值告警判断。
- `DataService._get_sensor_config_map()`：获取阈值配置映射。
- `DataService.prune_old_records()`：按天数清理历史数据。
- `DataService.prune_by_size()`：按数据库容量清理历史数据。

### 4.17 `server/app/services/latest_cache.py`
- `LatestCache.__init__()`：初始化线程安全缓存。
- `LatestCache.set()`：写入最新值。
- `LatestCache.get()`：读取最新值。

### 4.18 `server/app/services/media_storage.py`
- `_safe_name()`：生成安全文件名。
- `_media_dir()`：生成媒体目录。
- `store_image_base64()`：图像 Base64 落盘。
- `store_audio_bytes()`：音频字节落盘。
- `load_file_base64()`：从文件读取并编码 Base64。


### 4.20 `server/app/services/rfid_cache.py`
- `set_latest_rfid()`：写入最新 RFID。
- `get_latest_rfid()`：读取最新 RFID。
- `RFIDSnapshot`：RFID 快照数据结构。

### 4.21 `server/app/services/rtsp_capture.py`
- `configure_capture_options()`：解析 RTSP 采集参数。
- `open_capture()`：打开 RTSP 采集。
- `drain_capture()`：清空采集缓冲。
- `calc_drop_frames()`：计算需要丢弃的帧数。
- `RtspCaptureConfig`：RTSP 采集配置结构。

### 4.22 `server/app/services/rtsp_output.py`
- `create_rtsp_output()`：创建 RTSP 输出实例。
- `_parse_rtsp_url()`：解析 RTSP URL。
- `_build_caps()`：生成 GStreamer caps。
- `_build_gst_launch()`：生成 GStreamer launch 字符串。
- `_resolve_gst_encoder()`：选择编码器。
- `_build_encoder_props()`：构建编码器属性。
- `RtspOutputConfig`：RTSP 输出配置结构。
- `RtspOutput.start()`：启动 RTSP 输出。
- `RtspOutput.stop()`：停止输出。
- `RtspOutput.write_frame()`：写入一帧图像。
- `NullRtspOutput.start()`：空实现启动。
- `NullRtspOutput.stop()`：空实现停止。
- `NullRtspOutput.write_frame()`：空实现写入。
- `FFmpegRtspOutput.__init__()`：初始化 FFmpeg 输出。
- `FFmpegRtspOutput.start()`：启动 FFmpeg 推流。
- `FFmpegRtspOutput.stop()`：停止 FFmpeg 推流。
- `FFmpegRtspOutput.write_frame()`：向 FFmpeg 写帧。
- `FFmpegRtspOutput._terminate()`：终止 FFmpeg 进程。
- `GStreamerRtspOutput.__init__()`：初始化 GStreamer 输出。
- `GStreamerRtspOutput.start()`：启动 GStreamer 推流。
- `GStreamerRtspOutput.stop()`：停止推流。
- `GStreamerRtspOutput.write_frame()`：推送帧到管线。
- `GStreamerRtspOutput._run_loop()`：后台运行管线。
- `GStreamerRtspOutput._on_media_configure()`：配置 RTSP 媒体。

### 4.23 `server/app/services/runtime_supervisor.py`
- `RuntimeStatus`：运行状态数据结构。
- `RuntimeSupervisor.__init__()`：初始化守护对象。
- `RuntimeSupervisor.start()`：启动守护线程。
- `RuntimeSupervisor.stop()`：停止守护线程。
- `RuntimeSupervisor.get_status()`：获取运行状态。
- `RuntimeSupervisor.is_ready()`：就绪状态判断。
- `RuntimeSupervisor._run()`：主循环。
- `RuntimeSupervisor._start_retention_worker()`：启动数据保留线程。
- `RuntimeSupervisor._run_retention_loop()`：执行数据清理。
- `RuntimeSupervisor._ensure_mqtt()`：确保 MQTT 连接。
- `RuntimeSupervisor._ensure_db_initialized()`：确保数据库初始化。
- `RuntimeSupervisor._maybe_start_services()`：按配置启动 YOLO/音频。
- `RuntimeSupervisor._start_yolo_service()`：启动 YOLO 服务。
- `RuntimeSupervisor._start_audio_service()`：启动音频服务。
- `RuntimeSupervisor._ensure_worker_process()`：启动独立进程。
- `RuntimeSupervisor._stop_worker_processes()`：停止独立进程。
- `RuntimeSupervisor._ensure_command_timeouts()`：处理命令超时。
- `RuntimeSupervisor._wait()`：主循环等待。

### 4.24 `server/app/services/sensor_cache.py`
- `set_latest_sensor()`：写入最新传感器值。
- `get_latest_sensor()`：读取最新传感器值。

### 4.25 `server/app/services/audio_metrics.py`
- `compute_spectral_metrics()`：计算频谱特征。

### 4.26 `server/app/services/audio_service.py`
- `_ensure_audio_logging()`：配置音频日志。
- `_redact_rtsp_url()`：RTSP 地址脱敏。
- `_safe_filename_part()`：生成安全文件名片段。
- `AudioMonitorService.__init__()`：初始化音频服务。
- `AudioMonitorService.enabled()`：是否启用。
- `AudioMonitorService.start()`：启动采集线程。
- `AudioMonitorService.stop()`：停止采集线程。
- `AudioMonitorService._run()`：采集主循环。
- `AudioMonitorService._allocate_temp_wav_path()`：生成临时文件路径。
- `AudioMonitorService._capture_segment_to_wav()`：抓取片段到 WAV。
- `AudioMonitorService._store_wav_file()`：保存 WAV。
- `AudioMonitorService._inspect_wav_file()`：检查 WAV 是否有效。
- `AudioMonitorService._safe_file_size()`：安全获取文件大小。
- `AudioMonitorService._cleanup_file()`：清理临时文件。
- `AudioMonitorService._wait()`：循环等待。
- `AudioMonitorService.get_metrics()`：获取最新指标。
- `AudioMonitorService.update_thresholds()`：更新阈值。
- `AudioMonitorService._load_latest_thresholds()`：加载历史阈值。
- `AudioMonitorService._get_thresholds()`：当前阈值。
- `AudioMonitorService._evaluate_thresholds()`：阈值告警判断。

### 4.27 `server/app/services/audio_worker.py`
- `main()`：音频独立进程入口。

### 4.28 `server/app/services/bms_alerts.py`
- `maybe_create_bms_low_voltage_alarm()`：BMS 单体低压告警。

### 4.29 `server/app/services/bms_cache.py`
- `set_latest_bms()`：写入最新 BMS 值。
- `get_latest_bms()`：读取最新 BMS 值。

### 4.30 `server/app/services/yolo_service.py`
- `_ensure_console_logging()`：配置 YOLO 日志输出。
- `YOLOStreamService.__init__()`：初始化 YOLO 服务。
- `YOLOStreamService.enabled()`：是否启用。
- `YOLOStreamService.start()`：启动服务。
- `YOLOStreamService.stop()`：停止服务。
- `YOLOStreamService._run_loop()`：主循环。
- `YOLOStreamService._process_stream()`：处理视频流。
- `YOLOStreamService._handle_tracks()`：目标跟踪处理。
- `YOLOStreamService._emit_snapshots()`：输出截图并告警。
- `YOLOStreamService._resolve_location()`：解析位置字段。
- `YOLOStreamService._encode_frame()`：编码图像帧。
- `YOLOStreamService._publish_payload()`：发布检测结果。
- `YOLOStreamService._store_snapshot()`：保存截图。
- `YOLOStreamService._write_rtsp_frame()`：写入 RTSP 输出。
- `YOLOStreamService._start_rtsp_output()`：启动 RTSP 输出。
- `YOLOStreamService._stop_rtsp_output()`：停止 RTSP 输出。
- `YOLOStreamService._resolve_rtsp_backend()`：选择 RTSP 后端。
- `YOLOStreamService._load_model()`：加载模型。
- `YOLOStreamService._wait_with_stop()`：可中断等待。

### 4.31 `server/app/services/yolo_worker.py`
- `main()`：YOLO 独立进程入口。

### 4.32 `server/app/services/rfid_cache.py`
- `set_latest_rfid()`：写入最新 RFID。
- `get_latest_rfid()`：读取最新 RFID。
- `RFIDSnapshot`：RFID 快照结构。

### 4.33 `server/app/gui/app.py`
- `_GUILogHandler.__init__()`：初始化 GUI 日志处理器。
- `_GUILogHandler.emit()`：写入 GUI 日志。
- `MonitoringGUI.__init__()`：构建 GUI 主窗口。
- `MonitoringGUI._build_ui()`：构建 UI 布局。
- `MonitoringGUI._create_treeview()`：创建表格控件。
- `MonitoringGUI._build_commands_tab()`：构建命令选项卡。
- `MonitoringGUI._build_logs_tab()`：构建日志选项卡。
- `MonitoringGUI._build_config_tab()`：构建阈值配置选项卡。
- `MonitoringGUI._build_images_tab()`：构建图像选项卡。
- `MonitoringGUI._build_playback_tab()`：构建音频回放选项卡。
- `MonitoringGUI._build_audio_tab()`：构建音频实时监控选项卡。
- `MonitoringGUI._build_base_url()`：拼接 API 地址。
- `MonitoringGUI._schedule_refresh()`：定时刷新调度。
- `MonitoringGUI._refresh_all()`：触发全部刷新。
- `MonitoringGUI._refresh_data()`：刷新数据入口。
- `MonitoringGUI._refresh_data_thread()`：后台线程刷新。
- `MonitoringGUI._apply_refresh()`：应用刷新结果。
- `MonitoringGUI._handle_refresh_error()`：刷新错误处理。
- `MonitoringGUI._start_audio_monitor()`：启动音频监控。
- `MonitoringGUI._fetch_json()`：HTTP 拉取 JSON。
- `MonitoringGUI._update_tree()`：更新表格。
- `MonitoringGUI._update_command_history()`：更新命令历史。
- `MonitoringGUI._update_config_tree()`：更新阈值配置表。
- `MonitoringGUI._update_images_tab()`：更新图像区域。
- `MonitoringGUI._on_config_select()`：选择配置项。
- `MonitoringGUI._on_config_type_change()`：配置类型切换。
- `MonitoringGUI._apply_config_form()`：表单回填。
- `MonitoringGUI._save_config()`：保存阈值配置。
- `MonitoringGUI._delete_config()`：删除阈值配置。
- `MonitoringGUI._clear_config_form()`：清空表单。
- `MonitoringGUI._append_log()`：追加日志。
- `MonitoringGUI.append_log_from_thread()`：线程安全写日志。
- `MonitoringGUI._update_status()`：更新状态栏。
- `MonitoringGUI._on_image_selected()`：图像选中事件。
- `MonitoringGUI._render_image_preview()`：渲染图像预览。
- `MonitoringGUI._clear_image_preview()`：清理预览。
- `MonitoringGUI._save_audio_thresholds()`：保存音频阈值。
- `MonitoringGUI._refresh_audio_metrics()`：刷新音频指标。
- `MonitoringGUI._update_audio_chart()`：更新音频图表。
- `MonitoringGUI._update_audio_table()`：更新音频表格。
- `MonitoringGUI._save_selected_audio()`：保存选中音频。
- `MonitoringGUI._play_selected_audio()`：播放选中音频。
- `MonitoringGUI._stop_audio_playback()`：停止播放。
- `MonitoringGUI._find_audio_record()`：定位音频记录。
- `MonitoringGUI._start_playback()`：内部播放流程。
- `MonitoringGUI._stop_playback()`：内部停止播放。
- `MonitoringGUI._start_playback_external()`：调用外部播放器。
- `MonitoringGUI._stop_playback_external()`：停止外部播放器。
- `MonitoringGUI._read_playback_frame()`：读取播放帧。
- `MonitoringGUI._configure_logging()`：配置 GUI 日志。
- `MonitoringGUI.send_command()`：GUI 下发串口命令。
- `MonitoringGUI._normalize_payload()`：命令 payload 标准化。
- `MonitoringGUI._clear_command_inputs()`：清理命令输入。
- `MonitoringGUI._format_cell()`：格式化表格单元。
- `MonitoringGUI._extract_request_id()`：从返回体提取 request_id。
- `MonitoringGUI._generate_request_id()`：生成 request_id。
- `MonitoringGUI._safe_clear_tree()`：安全清空表格。
- `MonitoringGUI._check_bms_cell_voltages()`：检查 BMS 单体电压。
- `MonitoringGUI._handle_bms_alerts()`：处理 BMS 告警逻辑。
- `MonitoringGUI._prompt_bms_charge()`：弹出充电提示。
- `MonitoringGUI._send_bms_charge_command()`：发送充电命令。
- `MonitoringGUI._post_bms_alarm()`：上报告警。
- `MonitoringGUI._remind_charge_later()`：延后提醒。
- `MonitoringGUI.run()`：GUI 主循环。
- `main()`：GUI 入口。

### 4.34 `server/app/gui/audio_monitor.py`
- `AudioRTSPMonitor.__init__()`：初始化音频 RTSP 监控。
- `AudioRTSPMonitor.start()`：启动监控线程。
- `AudioRTSPMonitor.stop()`：停止监控线程。
- `AudioRTSPMonitor.get_latest()`：获取最新指标。
- `AudioRTSPMonitor._run()`：后台采集循环。
- `AudioRTSPMonitor._process_window()`：计算窗口特征。
- `AudioRTSPMonitor._spawn_ffmpeg()`：启动 FFmpeg。
- `AudioRTSPMonitor._terminate_ffmpeg()`：停止 FFmpeg。
- `AudioRTSPMonitor._wait()`：循环等待。

### 4.35 `server/app/schemas/*.py`
- `alarm.py`：`AlarmRecordRead` / `AlarmRecordCreate`（告警结构）。
- `audio.py`：`AudioDataBase` / `AudioDataCreate` / `AudioDataRead`。
- `bms.py`：`BMSDataBase` / `BMSDataCreate` / `BMSDataRead`。
- `command.py`：`CommandLogRead` / `CommandRequest` / `CommandRequestStatusRead`。
- `config.py`：`SensorConfigBase` / `SensorConfigCreate` / `SensorConfigRead`。
- `image.py`：`ImageDataBase` / `ImageDataCreate` / `ImageDataRead`。
- `metal.py`：`MetalAnomalyBase` / `MetalAnomalyCreate` / `MetalAnomalyRead`。
- `rfid.py`：`RFIDDataBase` / `RFIDDataCreate` / `RFIDDataRead`。
- `sensor.py`：`SensorDataBase` / `SensorDataCreate` / `SensorDataRead`。

### 4.36 `server/tests/*.py`
- `conftest.py: session()`：测试会话与临时数据库。
- `test_data_service.py`：阈值合并与告警逻辑测试。
- `test_ingestion_parsing.py`：MQTT 解析健壮性测试。
- `test_data_retention.py`：数据保留清理测试。
- `test_ingested_at.py`：更新为基础入库字段测试。
- `test_indexes.py`：索引存在性测试。
- `test_ingestion_fallback.py`：DB 失败降级告警测试。

### 4.37 `server/scripts/*.ps1` / `*.bat`
- 启动与环境切换脚本（Conda/Windows）。

## 5. 维护建议（稳定优先）
- 统一通过 `DataService` 进行入库，避免逻辑分散。
- 新增表时同步加入数据保留策略。
- MQTT 解析异常需走降级告警，不阻塞消费链路。
