# 文件讲解：`server/app/services/audio_metrics.py`

## 1. 文件定位
- **角色**：业务/基础模块
- **所在层级**：`server/app/services`
- **是否建议新人优先阅读**：是
- **模块文档摘要**：Audio feature extraction helpers (best-effort).

## 2. 对外依赖与协作关系
- **导入依赖（节选）**：
  - `from __future__ import annotations`
  - `logging`
  - `wave`
  - `from typing import Dict`

## 3. 核心模块与实现原理
- **函数设计**：
  - `compute_spectral_metrics(wav_path: str)`
    - 功能：Compute spectral features from a WAV file using librosa.
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：astype, audio.reshape, float, int, librosa.feature.rms, librosa.feature.spectral_bandwidth, librosa.feature.spectral_centroid, librosa.feature.spectral_flatness

## 4. 新人阅读建议（针对本文件）
- 建议先看公开函数/类，再沿“关键调用链”逐跳进入下层模块。
- 对 I/O 代码（MQTT、串口、DB）重点关注异常路径与重试策略。
- 可结合对应测试文件验证你对边界条件的理解。
