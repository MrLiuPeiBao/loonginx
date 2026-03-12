# 文件讲解：`server/app/services/rtsp_capture.py`

## 1. 文件定位
- **角色**：业务/基础模块
- **所在层级**：`server/app/services`
- **是否建议新人优先阅读**：是
- **模块文档摘要**：RTSP capture helpers focused on low-latency streaming.

## 2. 对外依赖与协作关系
- **导入依赖（节选）**：
  - `from __future__ import annotations`
  - `logging`
  - `os`
  - `from dataclasses import dataclass`
  - `from typing import Optional`

## 3. 核心模块与实现原理
- **类设计**：
  - `RtspCaptureConfig`（继承：无）
    - 作用：Configuration for RTSP capture.
    - 方法数量：0
- **函数设计**：
  - `configure_capture_options(options: str)`
    - 功能：Set OpenCV FFMPEG capture options if not already configured.
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：logger.info, os.environ.get
  - `open_capture(config: RtspCaptureConfig)`
    - 功能：Open a VideoCapture with optional low-latency tuning.
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：cap.set, configure_capture_options, cv2.VideoCapture, getattr, logger.debug, strip
  - `drain_capture(cap: 'cv2.VideoCapture', max_frames: int)`
    - 功能：Grab and drop frames to reduce backlog.
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：cap.grab, range
  - `calc_drop_frames(process_seconds: float, target_fps: int, max_drop_frames: int)`
    - 功能：Calculate how many frames to drop based on processing lag.
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：float, int, max, min

## 4. 新人阅读建议（针对本文件）
- 建议先看公开函数/类，再沿“关键调用链”逐跳进入下层模块。
- 对 I/O 代码（MQTT、串口、DB）重点关注异常路径与重试策略。
- 可结合对应测试文件验证你对边界条件的理解。
