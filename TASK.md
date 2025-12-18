# 当前任务

- [x] 服务器：整合 YOLOv8 RTSP 行人识别、截图存储与 MQTT 推送（topic: yolo/person_img）
- [x] 服务器：构建 FastAPI 服务，覆盖 MySQL 数据的增删查改、时间范围与最近 N 条查询
- [x] 服务器：搭建 Tkinter GUI，展示传感器/BMS/RFID/日志/命令等多标签页
- [x] 数据库：完善阈值、告警、图像、音频、金属异常等表结构及交互逻辑
- [x] 文档与配置：维护 README、环境变量加载（`python_dotenv`）、虚拟环境与依赖说明
- [x] 服务器：修复传感器数据在同一时间点的多指标合并存储问题，并确保入库结构一致
- [x] 服务器：实现传感器阈值判定逻辑，触发报警时写入 `alarm_records` 表
- [x] 测试：补充关键模块的单元/集成测试，并在 `venv_widows` 环境执行
- [x] 音频：按“ffmpeg 分段录制 WAV → 阈值判定 → 入库”重构音频入库路径，使用 `LONGBLOB` 存原始 WAV bytes（API/GUI 仍 Base64 传输），并增强日志观测
- [x] 服务器：增强启动健壮性（DB/MQTT 后台重试/ensure）并补充 liveness/readiness 健康检查
- [x] 服务器：统一通过 MQTT 广播告警事件（topic: `sensors/alarms`）
- [x] 启动：支持通过 `.env` 的 `GUI_ENABLED` 控制是否启动 GUI
- [x] 文档：补充项目启动流程工作原理说明
- [x] 工程：虚拟环境目录 `venv_linux` 更名为 `venv_widows` 并同步修正引用

## 工作期间发现

- 依赖清单与 README 已更新，后续变更需同步维护
- `/api/commands` 已通过 MQTT 发布，后续需增加失败重试与响应校验机制
- 图像/音频/金属异常接口需在真实硬件数据下验证载荷格式与存储策略
- MQTT 已支持传感器/BMS/RFID 数据入库，需在现场环境验证数据完整性与异常处理
- 已为各查询 API 增加 `offset` 支持，仍需前端对接分页参数
- Tkinter GUI 依赖正在运行的 FastAPI 服务以及 `/api/commands` 接口，请联调时确认网络与权限
- 前端如需统一实时告警通知，可订阅 MQTT `sensors/alarms`
