# 文件讲解：`client/communication/cableway_plc.py`

## 1. 文件定位
- **角色**：业务/基础模块
- **所在层级**：`client/communication`
- **是否建议新人优先阅读**：是

## 2. 对外依赖与协作关系
- **导入依赖（节选）**：
  - `from __future__ import annotations`
  - `struct`
  - `time`
  - `from dataclasses import dataclass`
  - `from datetime import datetime`
  - `from typing import Dict, Optional`
  - `from communication.cableway_specs import CONTROL_COMMANDS, FAULT_BITS, FLOAT_PARAMS, HEARTBEAT_VALUES, OUTPUT_BITS, VW_COMMAND, VW_ESTOP, VW_HEARTBEAT`
  - `from communication.modbus_tcp import ModbusTCPClient, ModbusTCPConfig, ModbusTCPError`
  - `from communication.plc_logging import get_plc_logger`

## 3. 核心模块与实现原理
- **类设计**：
  - `CablewayPLCConfig`（继承：无）
    - 方法数量：0
  - `CablewayPLC`（继承：无）
    - 作用：Cableway PLC integration via Modbus TCP (Siemens S7-200 SMART).
    - 方法数量：11
    - `__init__(self, config: CablewayPLCConfig)`
      - 关键调用链：ModbusTCPClient, ModbusTCPConfig, logger.info
    - `status_poll_interval(self)`
      - 关键调用链：float
    - `close(self)`
      - 关键调用链：logger.info, self._client.close
    - `ensure_connected(self)`
      - 关键调用链：logger.debug, self._client.connect
    - `poll_status_and_heartbeat(self)`
      - 关键调用链：bool, logger.debug, logger.error, logger.exception, logger.warning, self._maybe_send_heartbeat
    - `send_control_command(self, command_code: int, *, pulse: bool = True)`
      - 关键调用链：CONTROL_COMMANDS.get, ValueError, _vw_to_register, int, logger.info, self._write_pulse
    - `send_estop(self, *, pulse: bool = True)`
      - 关键调用链：_vw_to_register, logger.info, self._write_pulse
    - `set_params(self, params: Dict[str, float])`
      - 关键调用链：FLOAT_PARAMS.get, ValueError, _encode_float32_to_registers, _vd_to_register, float, len
    - ... 其余 3 个方法建议在 IDE 中按调用层级继续追踪
- **函数设计**：
  - `_now_iso()`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：astimezone, datetime.now, isoformat
  - `_now_ts()`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：astimezone, datetime.now, timestamp
  - `_vw_to_register(vw_address: int)`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：ValueError, int
  - `_vd_to_register(vd_address: int)`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：ValueError, int
  - `_encode_float32_to_registers(value: float, *, word_order: str = 'big', byte_order: str = 'big')`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：ValueError, float, struct.pack, struct.unpack
  - `_decode_bit_from_register(register_value: int, byte_address: int, bit_index: int, *, even_byte_is_high: bool)`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：ValueError, bool, int

## 4. 新人阅读建议（针对本文件）
- 建议先看公开函数/类，再沿“关键调用链”逐跳进入下层模块。
- 对 I/O 代码（MQTT、串口、DB）重点关注异常路径与重试策略。
- 可结合对应测试文件验证你对边界条件的理解。
