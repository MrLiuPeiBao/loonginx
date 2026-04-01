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
