# 文件讲解：`client/communication/serial_manager.py`

## 1. 文件定位
- **角色**：业务/基础模块
- **所在层级**：`client/communication`
- **是否建议新人优先阅读**：是

## 2. 对外依赖与协作关系
- **导入依赖（节选）**：
  - `logging`
  - `threading`
  - `time`
  - `from typing import List, Optional`
  - `minimalmodbus`
  - `serial`

## 3. 核心模块与实现原理
- **类设计**：
  - `SerialManager`（继承：无）
    - 作用：Thread-safe wrapper around minimalmodbus instruments.
    - 方法数量：16
    - `__init__(self, port: str, baudrate: int = 9600, timeout: float = 0.5, direct_retries: int = 3, direct_response_delay: float = 0.02, direct_timeout: Optional[float] = None)`
      - 关键调用链：threading.RLock
    - `_get_or_create_instrument(self, address: int)`
      - 关键调用链：logging.info, min, minimalmodbus.Instrument, self._instruments.get, serial_port.close
    - `get_instrument(self, address: int)`
      - 关键调用链：self._get_or_create_instrument
    - `read_registers(self, address: int, register: int, count: int, *, function_code: int = 3)`
      - 关键调用链：instrument.read_registers, logging.debug, logging.error, self._get_or_create_instrument
    - `read_registers_direct(self, address: int, register: int, count: int, *, function_code: int = 3, retries: Optional[int] = None, response_delay: Optional[float] = None, timeout: Optional[float] = None)`
      - 关键调用链：logging.debug, logging.error, logging.warning, range, self._build_read_request, self._extract_registers_from_frame
    - `send_raw_command(self, command: bytes)`
      - 关键调用链：bytes, command.hex, isinstance, len, logging.debug, logging.error
    - `_perform_direct_request(self, address: int, request: bytes, function_code: int, response_delay: float, timeout: float)`
      - 关键调用链：logging.error, max, self._drain_serial, self._get_or_create_instrument, self._read_register_response, serial_port.close
    - `_read_register_response(self, serial_port: serial.Serial, expected_address: int, function_code: int, timeout: float)`
      - 关键调用链：exception_code.hex, frame.hex, len, logging.debug, logging.error, max
    - ... 其余 8 个方法建议在 IDE 中按调用层级继续追踪

## 4. 新人阅读建议（针对本文件）
- 建议先看公开函数/类，再沿“关键调用链”逐跳进入下层模块。
- 对 I/O 代码（MQTT、串口、DB）重点关注异常路径与重试策略。
- 可结合对应测试文件验证你对边界条件的理解。
