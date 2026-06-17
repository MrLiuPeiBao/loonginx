# 文件讲解：`server/scripts/rtsp_audio_server.py`

## 1. 文件定位
- **角色**：业务/基础模块
- **所在层级**：`server/scripts`
- **是否建议新人优先阅读**：是

## 2. 对外依赖与协作关系
- **导入依赖（节选）**：
  - `from __future__ import annotations`
  - `argparse`
  - `sys`
  - `from pathlib import Path`
  - `from gi.repository import Gst, GstRtspServer, GLib`

## 3. 核心模块与实现原理
- **函数设计**：
  - `_build_pipeline(args: argparse.Namespace)`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：Path, SystemExit, expanduser, file_path.as_uri, file_path.exists, int, replace, resolve
  - `_attach_loop(factory: GstRtspServer.RTSPMediaFactory, args: argparse.Namespace)`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：bus.add_signal_watch, bus.connect, factory.connect, factory.set_eos_shutdown, media.get_element, pipeline.get_bus, pipeline.seek_simple, pipeline.set_state
  - `main()`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：GLib.MainLoop, Gst.init, GstRtspServer.RTSPMediaFactory, GstRtspServer.RTSPServer, _attach_loop, _build_pipeline, argparse.ArgumentParser, factory.set_launch

## 4. 新人阅读建议（针对本文件）
- 建议先看公开函数/类，再沿“关键调用链”逐跳进入下层模块。
- 对 I/O 代码（MQTT、串口、DB）重点关注异常路径与重试策略。
- 可结合对应测试文件验证你对边界条件的理解。
