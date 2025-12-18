# 项目规划

## 架构原则
- **语言与规范**：全项目使用 Python 3.9，遵循 PEP8、类型标注与 Google 风格文档字符串，必要时使用 `black` 格式化。
- **模块边界**：保持单文件不超过 500 行；按职责拆分模块，例如 `agent.py`（代理主逻辑）、`tools.py`（工具函数）、`prompts.py`（提示与模板）。
- **配置管理**：通过 `python_dotenv` 加载环境变量，提供 `load_env()` 辅助函数。
- **可扩展性**：为新增传感器或其他设备预留清晰接口；串口与 MQTT 交互需抽象成可组合的服务层。

## 运行与环境
- 使用 `venv_widows` 虚拟环境执行所有 Python 命令（含测试）。
- 依赖库：`asyncio`、`logging`、`minimalmodbus`、`serial`、`paho.mqtt` 等，必要时更新 `requirements` 说明。
- 数据库：MySQL（连接信息通过配置文件或环境变量读取），确保 ORM/SQL 层与业务逻辑隔离。

## 功能分区
1. **传感器采集层**
   - 通过 485 串口轮询各传感器（温湿度、气体、BMS 等），周期 500ms。
   - 解析协议数据，处理数值缩放、字节序转换。
   - 管理串口并发访问（定时轮询与指令下发需互斥）。
2. **RFID 接入层**
   - 监听 `/dev/ttyS0`（示例），识别读卡数据，维护最近卡号。
3. **MQTT 通信层**
   - 主题：
     - `sensors/data`（汇总环境数据）
     - `sensors/bms`（BMS 广播数据）
     - `sensors/rfid`（RFID 最新记录）
     - `sensors/command/request` & `response`
     - `yolo/person_img`
   - 支持订阅命令主题并回传执行结果。
4. **服务器端服务**
   - FastAPI 提供 REST API，负责 MySQL 数据增删查改、时间范围查询、最近 N 条查询。
   - 集成 MQTT 客户端与 WebSocket（如需实时推送）。
   - GUI（Tkinter）多标签页展示数据、日志、命令发送、配置管理、命令历史。
5. **数据持久化与告警**
   - 表：`sensor_config`、`sensor_data`、`alarm_records`、`image_data`、`audio_data`、`metal_anomaly`。
   - 提供阈值比对与告警记录逻辑。

## 文档与测试
- 更新 `README.md` 说明新功能、依赖与部署步骤。
- 保证关键逻辑具备单元测试或集成测试；测试中使用虚拟环境。
- 在 `TASK.md` 中维护任务状态，完成即勾选，新增事项写入“工作期间发现”。

## 未决问题
- 需确认现有代码结构与数据库模型是否与上述规划一致。
- Tkinter GUI 与 FastAPI 协同运行的方式（同进程协程或分进程）。
- 是否需要在 Windows 环境下提供串口模拟方案以便开发调试。
