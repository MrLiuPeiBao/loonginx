"""索道 PLC（西门子 S7-200 SMART）点表与校验逻辑。

来源：`机器人索道通信格式与点表.xlsx`（Sheet1）。

要点：
- PLC 作为 Modbus TCP 服务器（默认 IP：192.168.2.1）。
- 200Smart 对应 Modbus Holding Register 地址换算：字节地址 / 2 + 1（人类 1-based），
  代码实现时通常使用 0-based 地址：字节地址 / 2。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple


VW_COMMAND: int = 2404
VW_ESTOP: int = 2410
VW_HEARTBEAT: int = 2414

HEARTBEAT_VALUES: Tuple[int, int] = (0, 5)


CONTROL_COMMANDS: Dict[int, str] = {
    101: '回原点',
    201: '自动往复正向启动',
    202: '自动往复反向启动',
    301: '定位启动',
    401: '点动前进',
    402: '点动后退',
    88: '停止',
    66: '故障复位',
    0: '无命令',
}


@dataclass(frozen=True)
class FloatParamSpec:
    key: str
    name: str
    vd_address: int
    min_value: Optional[float]
    max_value: Optional[float]
    unit: str
    note: str = ''


FLOAT_PARAMS: Dict[str, FloatParamSpec] = {
    'cs_speed_limit': FloatParamSpec('cs_speed_limit', 'CS速度上限', 2048, 0.0, 1.0, '米/秒', '范围 0~1'),
    'cs_remote_position': FloatParamSpec('cs_remote_position', 'CS远控位置', 2044, 0.0, 999.0, '米', '范围0~999.0'),
    'cs_decel_distance': FloatParamSpec('cs_decel_distance', 'CS减速距离', 2036, 0.0, 999.0, '米', '范围0~999.0'),
    'cs_max_speed': FloatParamSpec('cs_max_speed', 'CS最大速度', 2032, 0.0, 1.0, '米/秒', '0~1.0'),
    'cs_home_speed': FloatParamSpec('cs_home_speed', 'CS原点速度', 2028, 0.0, 0.6, '米/秒', '范围 0~0.6'),
    'cs_manual_speed': FloatParamSpec('cs_manual_speed', 'CS手动速度', 2024, 0.0, 0.6, '米/秒', '范围 0~0.6'),
    'cs_auto_speed': FloatParamSpec('cs_auto_speed', 'CS自动速度', 2020, 0.0, 0.6, '米/秒', '范围 0~0.6'),
    'cs_pulse_equivalent': FloatParamSpec('cs_pulse_equivalent', 'CS脉冲当量', 2016, 0.0, 1.0, '米/PULL', '范围0.00~1'),
    'cs_positive_limit': FloatParamSpec('cs_positive_limit', 'CS正限位', 2012, 0.0, 999.0, '米', '范围0~999.0'),
    'cs_negative_limit': FloatParamSpec('cs_negative_limit', 'CS负限位', 2008, -10.0, 0.0, '米', '范围0~-10.0'),
    'cs_allowed_deviation': FloatParamSpec('cs_allowed_deviation', 'CS允许偏差', 2004, 0.0, 1.0, '米', '范围0~1.0'),
    'cs_end_position': FloatParamSpec('cs_end_position', 'CS终点位置', 2000, 0.0, 999.0, '米', '范围0~999.0'),
}


def validate_control_command_code(code: int) -> int:
    """校验 VW2404 控制命令码。"""
    value = int(code)
    if value not in CONTROL_COMMANDS:
        raise ValueError(f'不支持的控制命令码: {value}')
    return value


def validate_param_updates(params: Dict[str, float]) -> Dict[str, float]:
    """校验参数更新请求（按点表范围限制）。"""
    if not isinstance(params, dict) or not params:
        raise ValueError('params 必须是非空对象')

    validated: Dict[str, float] = {}
    for key, raw_value in params.items():
        spec = FLOAT_PARAMS.get(str(key))
        if not spec:
            raise ValueError(f'未知参数: {key}')
        value = float(raw_value)
        if spec.min_value is not None and value < spec.min_value:
            raise ValueError(f'{key} 低于下限 {spec.min_value}: {value}')
        if spec.max_value is not None and value > spec.max_value:
            raise ValueError(f'{key} 高于上限 {spec.max_value}: {value}')
        validated[str(key)] = value
    return validated
