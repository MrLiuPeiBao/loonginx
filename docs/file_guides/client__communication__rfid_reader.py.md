# 文件讲解：`client/communication/rfid_reader.py`

## 1. 文件定位
- **角色**：业务/基础模块
- **所在层级**：`client/communication`
- **是否建议新人优先阅读**：是

## 2. 对外依赖与协作关系
- **导入依赖（节选）**：
  - `logging`
  - `threading`
  - `from datetime import datetime`
  - `from typing import Callable, Optional`
  - `serial`

## 3. 核心模块与实现原理
- **类设计**：
  - `RFIDReader`（继承：无）
    - 作用：Background thread that streams RFID serial data via callback.
    - 方法数量：7
    - `__init__(self, port: str, baudrate: int = 9600)`
    - `start_reading(self, callback: Callable)`
      - 职责：Start the reader thread.
      - 关键调用链：logging.error, logging.info, self.thread.start, serial.Serial, threading.Thread
    - `_read_loop(self)`
      - 职责：Consume CRLF-terminated frames and emit via callback.
      - 关键调用链：data.hex, len, logging.error, logging.info, self._get_timestamp, self._get_ts
    - `_get_timestamp()`
      - 关键调用链：astimezone, datetime.now, isoformat
    - `_get_ts()`
      - 关键调用链：astimezone, datetime.now, timestamp
    - `stop_reading(self)`
      - 职责：Stop the reader thread.
      - 关键调用链：logging.info, self.serial.close, self.thread.join
    - `is_reading(self)`
      - 关键调用链：bool

## 4. 新人阅读建议（针对本文件）
- 建议先看公开函数/类，再沿“关键调用链”逐跳进入下层模块。
- 对 I/O 代码（MQTT、串口、DB）重点关注异常路径与重试策略。
- 可结合对应测试文件验证你对边界条件的理解。
