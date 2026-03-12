# 文件讲解：`client/communication/modbus_tcp.py`

## 1. 文件定位
- **角色**：业务/基础模块
- **所在层级**：`client/communication`
- **是否建议新人优先阅读**：是

## 2. 对外依赖与协作关系
- **导入依赖（节选）**：
  - `from __future__ import annotations`
  - `socket`
  - `struct`
  - `threading`
  - `from dataclasses import dataclass`
  - `from typing import List, Optional`
  - `from communication.plc_logging import get_plc_logger`

## 3. 核心模块与实现原理
- **类设计**：
  - `ModbusTCPError`（继承：RuntimeError）
    - 作用：Raised when a Modbus TCP request fails.
    - 方法数量：0
  - `ModbusTCPConfig`（继承：无）
    - 方法数量：0
  - `ModbusTCPClient`（继承：无）
    - 作用：Minimal Modbus TCP client for holding register read/write (FC03/06/16).
    - 方法数量：8
    - `__init__(self, config: ModbusTCPConfig)`
      - 关键调用链：threading.RLock
    - `is_connected(self)`
    - `connect(self)`
      - 关键调用链：ModbusTCPError, float, int, logger.debug, logger.error, logger.info
    - `close(self)`
      - 关键调用链：logger.info, self._sock.close
    - `read_holding_registers(self, address: int, count: int)`
      - 关键调用链：ModbusTCPError, ValueError, int, len, list, logger.debug
    - `write_single_register(self, address: int, value: int)`
      - 关键调用链：ModbusTCPError, int, len, logger.debug, self._request, struct.pack
    - `write_multiple_registers(self, address: int, values: List[int])`
      - 关键调用链：ModbusTCPError, ValueError, int, len, logger.debug, self._request
    - `_request(self, pdu: bytes)`
      - 关键调用链：ModbusTCPError, _recv_exact, int, len, logger.debug, logger.error
- **函数设计**：
  - `_recv_exact(sock: socket.socket, size: int)`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：ModbusTCPError, bytearray, bytes, data.extend, len, sock.recv

## 4. 新人阅读建议（针对本文件）
- 建议先看公开函数/类，再沿“关键调用链”逐跳进入下层模块。
- 对 I/O 代码（MQTT、串口、DB）重点关注异常路径与重试策略。
- 可结合对应测试文件验证你对边界条件的理解。
