# 文件讲解：`client/tools/modbus_rtu_sim.py`

## 1. 文件定位
- **角色**：业务/基础模块
- **所在层级**：`client/tools`
- **是否建议新人优先阅读**：是

## 2. 对外依赖与协作关系
- **导入依赖（节选）**：
  - `argparse`
  - `json`
  - `os`
  - `pty`
  - `select`
  - `struct`
  - `sys`
  - `time`
  - `tty`
  - `from typing import Dict, Optional, Tuple`

## 3. 核心模块与实现原理
- **类设计**：
  - `RangeGenerator`（继承：无）
    - 方法数量：2
    - `__init__(self, min_value: float, max_value: float, step: float)`
      - 关键调用链：float
    - `next(self)`
  - `ModbusSimulator`（继承：无）
    - 方法数量：7
    - `__init__(self, overrides: Dict[str, Dict[str, float]])`
      - 关键调用链：RangeGenerator, self._apply_overrides, self.bms.items, self.sensors.items
    - `_apply_overrides(self, overrides: Dict[str, Dict[str, float]])`
      - 关键调用链：isinstance, override.items, overrides.items, self._apply_range
    - `_apply_range(target: Dict[str, float], override: Dict[str, float])`
      - 关键调用链：float
    - `handle_request(self, address: int, function: int, register: int, count: int)`
      - 关键调用链：encode_dcba, int, next, round, self._build_response, self._exception
    - `_handle_bms(self, address: int, function: int, register: int, count: int)`
      - 关键调用链：cell_cfg.get, cell_values.append, encode_scaled, next, range, registers.append
    - `_build_response(address: int, function: int, registers: Tuple[int, ...])`
      - 关键调用链：add_crc, bytes, int, join, len, to_bytes
    - `_exception(address: int, function: int, code: int)`
      - 关键调用链：add_crc, bytes
- **函数设计**：
  - `crc16_modbus(data: bytes)`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：range
  - `add_crc(payload: bytes)`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：crc.to_bytes, crc16_modbus
  - `parse_request(frame: bytes)`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：crc16_modbus, int.from_bytes, len
  - `encode_dcba(value: float)`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：bytes, float, int.from_bytes, reversed, struct.pack
  - `encode_scaled(value: float, multiplier: float, offset: float)`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：int, round
  - `load_overrides(path: str)`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：isinstance, json.load, open, os.path.exists
  - `run(link_path: str, config_path: str)`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：ModbusSimulator, buffer.extend, buffer.pop, bytearray, bytes, len, load_overrides, os.path.exists
  - `main()`
    - 实现原理：通过输入参数与模块状态计算结果，并组织 I/O、解析或编排逻辑。
    - 依赖函数：argparse.ArgumentParser, parser.add_argument, parser.parse_args, run

## 4. 新人阅读建议（针对本文件）
- 建议先看公开函数/类，再沿“关键调用链”逐跳进入下层模块。
- 对 I/O 代码（MQTT、串口、DB）重点关注异常路径与重试策略。
- 可结合对应测试文件验证你对边界条件的理解。
