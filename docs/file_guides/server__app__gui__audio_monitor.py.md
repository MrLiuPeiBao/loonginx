# 文件讲解：`server/app/gui/audio_monitor.py`

## 1. 文件定位
- **角色**：业务/基础模块
- **所在层级**：`server/app/gui`
- **是否建议新人优先阅读**：是
- **模块文档摘要**：Lightweight RTSP audio monitor for GUI-side real-time metrics.

## 2. 对外依赖与协作关系
- **导入依赖（节选）**：
  - `from __future__ import annotations`
  - `logging`
  - `subprocess`
  - `threading`
  - `time`
  - `from typing import Dict, Optional`
  - `numpy as np`
  - `librosa`

## 3. 核心模块与实现原理
- **类设计**：
  - `AudioRTSPMonitor`（继承：无）
    - 作用：Read audio from RTSP via ffmpeg and expose latest spectral metrics.
    - 方法数量：9
    - `__init__(self, url: str, sample_rate: int = 16000, window_seconds: float = 1.0)`
      - 关键调用链：int, max, threading.Event, threading.Lock
    - `start(self)`
      - 关键调用链：logger.info, self._stop_event.clear, self._thread.is_alive, self._thread.start, threading.Thread
    - `stop(self)`
      - 关键调用链：self._stop_event.set, self._terminate_ffmpeg, self._thread.is_alive, self._thread.join
    - `get_latest(self)`
      - 关键调用链：dict
    - `_run(self)`
      - 关键调用链：buffer.extend, bytearray, decode, len, logger.exception, logger.warning
    - `_process_window(self, raw_bytes: bytes)`
      - 关键调用链：astype, float, librosa.feature.spectral_bandwidth, librosa.feature.spectral_centroid, librosa.feature.spectral_flatness, librosa.feature.spectral_rolloff
    - `_spawn_ffmpeg(self)`
      - 关键调用链：logger.error, logger.exception, str, subprocess.Popen
    - `_terminate_ffmpeg(self)`
      - 关键调用链：proc.kill, proc.terminate, proc.wait
    - ... 其余 1 个方法建议在 IDE 中按调用层级继续追踪

## 4. 新人阅读建议（针对本文件）
- 建议先看公开函数/类，再沿“关键调用链”逐跳进入下层模块。
- 对 I/O 代码（MQTT、串口、DB）重点关注异常路径与重试策略。
- 可结合对应测试文件验证你对边界条件的理解。
