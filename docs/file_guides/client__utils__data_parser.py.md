# 文件讲解：`client/utils/data_parser.py`

## 1. 文件定位
- **角色**：业务/基础模块
- **所在层级**：`client/utils`
- **是否建议新人优先阅读**：是

## 2. 对外依赖与协作关系
- **导入依赖（节选）**：
  - `logging`
  - `struct`
  - `from typing import Optional`

## 3. 核心模块与实现原理
- **类设计**：
  - `DataParser`（继承：无）
    - 作用：Helper functions for decoding Modbus register payloads.
    - 方法数量：5
    - `parse_dcba(data_bytes: bytes)`
      - 职责：Decode a 32-bit float stored as DCBA.
      - 关键调用链：ValueError, bytes, data_bytes.hex, len, logging.error, reversed
    - `parse_raw(data_bytes: bytes)`
      - 职责：Decode an unsigned 16-bit integer.
      - 关键调用链：ValueError, data_bytes.hex, int.from_bytes, len, logging.error
    - `parse_o2(data_bytes: bytes)`
      - 职责：O2 sensor payload scaled by 10.
      - 关键调用链：DataParser.parse_raw, round
    - `parse_smoke(data_bytes: bytes)`
      - 职责：Smoke sensor payload scaled by 10.
      - 关键调用链：DataParser.parse_raw, round
    - `parse_bms_data(data_bytes: bytes, multiplier: float = 1.0, offset: float = 0.0, precision: int = 3)`
      - 职责：Scale BMS register value with optional offset and precision controls.
      - 关键调用链：DataParser.parse_raw, round

## 4. 新人阅读建议（针对本文件）
- 建议先看公开函数/类，再沿“关键调用链”逐跳进入下层模块。
- 对 I/O 代码（MQTT、串口、DB）重点关注异常路径与重试策略。
- 可结合对应测试文件验证你对边界条件的理解。
