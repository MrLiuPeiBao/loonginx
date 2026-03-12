# 文件讲解：`server/app/services/yolo_service.py`

## 1. 文件定位
- **角色**：业务/基础模块
- **所在层级**：`server/app/services`
- **是否建议新人优先阅读**：是
- **模块文档摘要**：YOLOv8 RTSP 行人识别与截图推送服务.

## 2. 对外依赖与协作关系
- **导入依赖（节选）**：
  - `from __future__ import annotations`
  - `base64`
  - `json`
  - `logging`
  - `threading`
  - `time`
  - `from datetime import datetime`
  - `from pathlib import Path`
  - `from typing import Any, Dict, Optional`
  - `sys`
  - `from app.core.config import Settings`
  - `from app.core.constants import MQTT_TOPICS`
  - `from app.db.models import ImageData`
  - `from app.db.session import session_scope`
  - `from app.mqtt import MQTTManager`
  - `from app.services.alarm_publisher import build_alarm_event, publish_alarm_event`
  - `from app.services.data_service import DataService`
  - `from app.services.rtsp_capture import RtspCaptureConfig, calc_drop_frames, drain_capture, open_capture`
  - `from app.services.rtsp_output import RtspOutput, RtspOutputConfig, create_rtsp_output`

## 3. 核心模块与实现原理
- **类设计**：
  - `YOLOStreamService`（继承：无）
    - 作用：Stream RTSP frames, run YOLO detection, and publish person snapshots.
    - 方法数量：19
    - `__init__(self, settings: Settings, mqtt_manager: MQTTManager)`
      - 职责：Initialize service with runtime dependencies.
      - 关键调用链：logger.info, self._device.upper, threading.Event, threading.Lock, torch.cuda.is_available
    - `enabled(self)`
      - 职责：Check whether background worker can be started.
      - 关键调用链：bool
    - `start(self)`
      - 职责：Spawn background worker when dependencies and config allow.
      - 关键调用链：bool, logger.info, logger.warning, self._stop_event.clear, self._thread.is_alive, self._thread.start
    - `stop(self, timeout: float = 5.0)`
      - 职责：Signal worker to stop and wait for graceful exit.
      - 关键调用链：logger.info, self._stop_event.set, self._stop_rtsp_output, thread.is_alive, thread.join
    - `apply_settings(self, settings: Settings)`
      - 职责：Apply new settings and restart background worker if needed.
      - 关键调用链：self.start, self.stop
    - `_run_loop(self)`
      - 职责：Run RTSP capture loop with automatic reconnects.
      - 关键调用链：RtspCaptureConfig, cap.isOpened, cap.release, int, logger.error, logger.exception
    - `_process_stream(self, cap: 'cv2.VideoCapture')`
      - 职责：Track persons, push snapshots, and forward annotated frames.
      - 关键调用链：bool, calc_drop_frames, cap.read, drain_capture, float, int
    - `_handle_tracks(self, raw_frame: Any, annotated_frame: Any, results: Any, detection_interval: float)`
      - 职责：Emit snapshots for tracked persons with per-track cooldown.
      - 关键调用链：coords.tolist, datetime.now, getattr, hasattr, int, logger.debug
    - ... 其余 11 个方法建议在 IDE 中按调用层级继续追踪
- **函数设计**：
  - `_ensure_console_logging()`
    - 功能：Attach a dedicated console handler for YOLO logs.
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：console_handler.setFormatter, console_handler.setLevel, getattr, logger.addHandler, logging.Formatter, logging.StreamHandler, setattr

## 4. 新人阅读建议（针对本文件）
- 建议先看公开函数/类，再沿“关键调用链”逐跳进入下层模块。
- 对 I/O 代码（MQTT、串口、DB）重点关注异常路径与重试策略。
- 可结合对应测试文件验证你对边界条件的理解。
