# 文件讲解：`server/app/services/runtime_supervisor.py`

## 1. 文件定位
- **角色**：业务/基础模块
- **所在层级**：`server/app/services`
- **是否建议新人优先阅读**：是
- **模块文档摘要**：Runtime supervision for startup dependencies.

## 2. 对外依赖与协作关系
- **导入依赖（节选）**：
  - `from __future__ import annotations`
  - `logging`
  - `threading`
  - `time`
  - `subprocess`
  - `sys`
  - `from pathlib import Path`
  - `from dataclasses import dataclass`
  - `from typing import Dict, Optional`
  - `from app.db.session import db_ping, init_db, session_scope`
  - `from app.services.data_service import DataService`
  - `from app.db.models import CommandStatus`
  - `from app.core.config import Settings`
  - `from app.mqtt import MQTTManager`
  - `from app.services.audio_service import AudioMonitorService`
  - `from app.services.yolo_service import YOLOStreamService`

## 3. 核心模块与实现原理
- **类设计**：
  - `RuntimeStatus`（继承：无）
    - 作用：Snapshot for readiness/liveness reporting.
    - 方法数量：0
  - `RuntimeSupervisor`（继承：无）
    - 作用：Continuously ensure DB/MQTT and start background services when ready.
    - 方法数量：21
    - `__init__(self, *, mqtt_manager: MQTTManager, yolo_service: YOLOStreamService, audio_service: AudioMonitorService, settings: Settings, poll_seconds: float = 2.0, db_retry_max_seconds: float = 30.0)`
      - 职责：Create a supervisor instance.
      - 关键调用链：RuntimeStatus, bool, float, int, max, threading.Event
    - `apply_settings(self, settings: Settings, *, changed_keys: Optional[list[str]] = None)`
      - 职责：Apply new settings to supervisor runtime.
      - 关键调用链：bool, int, max, self._handle_runtime_changes, self._start_retention_worker
    - `start(self)`
      - 职责：Start supervisor thread (idempotent).
      - 关键调用链：logger.info, self._start_retention_worker, self._stop_event.clear, self._thread.is_alive, self._thread.start, threading.Thread
    - `stop(self, timeout: float = 3.0)`
      - 职责：Stop supervisor thread.
      - 关键调用链：logger.info, retention_thread.is_alive, retention_thread.join, self._stop_event.set, self._stop_worker_processes, thread.is_alive
    - `get_status(self)`
      - 职责：Get a thread-safe status snapshot.
    - `is_ready(self)`
      - 职责：Return readiness state for watchdog.
      - 关键调用链：bool
    - `_run(self)`
      - 关键调用链：bool, db_ping, max, min, self._audio_process.poll, self._ensure_command_timeouts
    - `_start_retention_worker(self)`
      - 关键调用链：self._retention_thread.is_alive, self._retention_thread.start, threading.Thread
    - ... 其余 13 个方法建议在 IDE 中按调用层级继续追踪

## 4. 新人阅读建议（针对本文件）
- 建议先看公开函数/类，再沿“关键调用链”逐跳进入下层模块。
- 对 I/O 代码（MQTT、串口、DB）重点关注异常路径与重试策略。
- 可结合对应测试文件验证你对边界条件的理解。
