# 文件讲解：`server/app/services/audio_service.py`

## 1. 文件定位
- **角色**：业务/基础模块
- **所在层级**：`server/app/services`
- **是否建议新人优先阅读**：是
- **模块文档摘要**：Audio recording service for RTSP streams.

## 2. 对外依赖与协作关系
- **导入依赖（节选）**：
  - `from __future__ import annotations`
  - `logging`
  - `math`
  - `os`
  - `subprocess`
  - `sys`
  - `tempfile`
  - `threading`
  - `time`
  - `wave`
  - `from array import array`
  - `from pathlib import Path`
  - `from datetime import datetime`
  - `from typing import Deque, Dict, Optional`
  - `from collections import deque`
  - `from urllib.parse import urlsplit, urlunsplit`
  - `from sqlmodel import select`
  - `from app.core.config import Settings`
  - `from app.db.audio_thresholds import AudioThreshold`
  - `from app.db.models import AudioData`
  - ... 共 25 项

## 3. 核心模块与实现原理
- **类设计**：
  - `AudioMonitorService`（继承：无）
    - 作用：Continuously record RTSP audio and persist into database.
    - 方法数量：20
    - `__init__(self, settings: Settings, mqtt_manager: Optional[MQTTManager] = None)`
      - 关键调用链：deque, threading.Event, threading.Lock
    - `enabled(self)`
      - 关键调用链：bool
    - `start(self)`
      - 关键调用链：bool, logger.debug, logger.warning, self._load_latest_thresholds, self._stop_event.clear, self._thread.is_alive
    - `stop(self)`
      - 关键调用链：logger.debug, logger.warning, self._stop_event.set, self._thread.is_alive, self._thread.join
    - `apply_settings(self, settings: Settings)`
      - 职责：Apply new settings and restart background worker if needed.
      - 关键调用链：lower, self.start, self.stop
    - `_run(self)`
      - 关键调用链：_redact_rtsp_url, bool, evaluation.get, float, int, logger.debug
    - `_allocate_temp_wav_path(self)`
      - 职责：Allocate a temp path for ffmpeg output wav.
      - 关键调用链：logger.debug, os.close, tempfile.mkstemp
    - `_capture_segment_to_wav(self, *, url: str, sample_rate: int, duration_seconds: float, output_path: str)`
      - 职责：Run ffmpeg to capture one WAV segment file.
      - 关键调用链：_redact_rtsp_url, float, logger.debug, logger.error, logger.exception, logger.warning
    - ... 其余 12 个方法建议在 IDE 中按调用层级继续追踪
- **函数设计**：
  - `_ensure_audio_logging()`
    - 功能：Ensure audio logs are written to console and file for detailed debugging.
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：Path, console_handler.setFormatter, console_handler.setLevel, file_handler.setFormatter, file_handler.setLevel, getattr, log_path.parent.mkdir, logger.addHandler
  - `_redact_rtsp_url(url: str)`
    - 功能：Redact credentials in RTSP URL for safer logging.
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：urlsplit, urlunsplit
  - `_safe_filename_part(text: str, *, max_len: int = 40)`
    - 功能：Convert arbitrary text into a filesystem-safe short token.
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：ch.isalnum, cleaned.strip, join

## 4. 新人阅读建议（针对本文件）
- 建议先看公开函数/类，再沿“关键调用链”逐跳进入下层模块。
- 对 I/O 代码（MQTT、串口、DB）重点关注异常路径与重试策略。
- 可结合对应测试文件验证你对边界条件的理解。
