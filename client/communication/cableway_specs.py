from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple


# Specs source: 机器人索道通信格式与点表.xlsx (Sheet1)

# Siemens S7-200 SMART note in the spec:
# Modbus holding register number = (V/VW/VD byte address) / 2 + 1, i.e. 0-based address is byte_address / 2.


VW_COMMAND: int = 2404
VW_ESTOP: int = 2410
VW_HEARTBEAT: int = 2414

HEARTBEAT_VALUES: Tuple[int, int] = (0, 5)


CONTROL_COMMANDS: Dict[int, str] = {
    101: "回原点",
    201: "自动往复正向启动",
    202: "自动往复反向启动",
    301: "定位启动",
    401: "点动前进",
    402: "点动后退",
    88: "停止",
    66: "故障复位",
    0: "无命令",
}


@dataclass(frozen=True)
class FloatParamSpec:
    key: str
    name: str
    vd_address: int
    min_value: Optional[float]
    max_value: Optional[float]
    unit: str
    note: str = ""


FLOAT_PARAMS: Dict[str, FloatParamSpec] = {
    "cs_speed_limit": FloatParamSpec(
        key="cs_speed_limit",
        name="CS速度上限",
        vd_address=2048,
        min_value=0.0,
        max_value=1.0,
        unit="米/秒",
        note="范围 0~1",
    ),
    "cs_remote_position": FloatParamSpec(
        key="cs_remote_position",
        name="CS远控位置",
        vd_address=2044,
        min_value=0.0,
        max_value=999.0,
        unit="米",
        note="范围0~999.0",
    ),
    "cs_decel_distance": FloatParamSpec(
        key="cs_decel_distance",
        name="CS减速距离",
        vd_address=2036,
        min_value=0.0,
        max_value=999.0,
        unit="米",
        note="范围0~999.0",
    ),
    "cs_max_speed": FloatParamSpec(
        key="cs_max_speed",
        name="CS最大速度",
        vd_address=2032,
        min_value=0.0,
        max_value=1.0,
        unit="米/秒",
        note="0~1.0",
    ),
    "cs_home_speed": FloatParamSpec(
        key="cs_home_speed",
        name="CS原点速度",
        vd_address=2028,
        min_value=0.0,
        max_value=0.6,
        unit="米/秒",
        note="范围 0~0.6",
    ),
    "cs_manual_speed": FloatParamSpec(
        key="cs_manual_speed",
        name="CS手动速度",
        vd_address=2024,
        min_value=0.0,
        max_value=0.6,
        unit="米/秒",
        note="范围 0~0.6",
    ),
    "cs_auto_speed": FloatParamSpec(
        key="cs_auto_speed",
        name="CS自动速度",
        vd_address=2020,
        min_value=0.0,
        max_value=0.6,
        unit="米/秒",
        note="范围 0~0.6",
    ),
    "cs_pulse_equivalent": FloatParamSpec(
        key="cs_pulse_equivalent",
        name="CS脉冲当量",
        vd_address=2016,
        min_value=0.0,
        max_value=1.0,
        unit="米/PULL",
        note="范围0.00~1",
    ),
    "cs_positive_limit": FloatParamSpec(
        key="cs_positive_limit",
        name="CS正限位",
        vd_address=2012,
        min_value=0.0,
        max_value=999.0,
        unit="米",
        note="范围0~999.0",
    ),
    "cs_negative_limit": FloatParamSpec(
        key="cs_negative_limit",
        name="CS负限位",
        vd_address=2008,
        min_value=-10.0,
        max_value=0.0,
        unit="米",
        note="范围0~-10.0",
    ),
    "cs_allowed_deviation": FloatParamSpec(
        key="cs_allowed_deviation",
        name="CS允许偏差",
        vd_address=2004,
        min_value=0.0,
        max_value=1.0,
        unit="米",
        note="范围0~1.0",
    ),
    "cs_end_position": FloatParamSpec(
        key="cs_end_position",
        name="CS终点位置",
        vd_address=2000,
        min_value=0.0,
        max_value=999.0,
        unit="米",
        note="范围0~999.0",
    ),
}


@dataclass(frozen=True)
class BitSpec:
    key: str
    name: str
    byte_address: int
    bit_index: int
    note: str = ""


FAULT_BITS: Dict[str, BitSpec] = {
    "gz_total_fault": BitSpec("gz_total_fault", "GZ总故障", 2889, 7),
    "gz_position_fault": BitSpec("gz_position_fault", "GZ位置故障", 2884, 6),
    "gz_home_fault": BitSpec("gz_home_fault", "GZ原点故障", 2884, 5),
    "gz_over_positive_limit": BitSpec("gz_over_positive_limit", "GZ位置超正限", 2884, 3),
    "gz_over_negative_limit": BitSpec("gz_over_negative_limit", "GZ位置超负限", 2884, 2),
    "gz_deviation_too_large": BitSpec("gz_deviation_too_large", "GZ偏差过大", 2884, 1),
    "gz_setpoint_overrun": BitSpec("gz_setpoint_overrun", "GZ给定超限", 2884, 0),
    "gz_hard_limit": BitSpec("gz_hard_limit", "GZ硬限位", 2883, 2),
    "gz_positive_limit_estop": BitSpec("gz_positive_limit_estop", "GZ正限急停", 2883, 1),
    "gz_negative_limit_estop": BitSpec("gz_negative_limit_estop", "GZ负限急停", 2883, 0),
    "gz_estop_fault": BitSpec("gz_estop_fault", "GZ急停故障", 2882, 2),
    "gz_estop_inhibit_start": BitSpec("gz_estop_inhibit_start", "GZ急停禁启", 2882, 1),
    "gz_robot_estop": BitSpec("gz_robot_estop", "GZ机器人急停", 2882, 0),
    "gz_general_fault": BitSpec("gz_general_fault", "GZ普通故障", 2880, 5),
    "gz_counterweight_low": BitSpec("gz_counterweight_low", "GZ重锤下限", 2880, 4),
    "gz_counterweight_high": BitSpec("gz_counterweight_high", "GZ重锤上限", 2880, 3),
    "gz_overspeed": BitSpec("gz_overspeed", "GZ超速", 2880, 2),
    "gz_vfd_fault": BitSpec("gz_vfd_fault", "GZ变频故障", 2880, 1),
    "gz_brake_fault": BitSpec("gz_brake_fault", "GZ报闸故障", 2880, 0),
}


OUTPUT_BITS: Dict[str, BitSpec] = {
    "q_fault": BitSpec("q_fault", "Q故障", 2810, 4),
    "q_reverse": BitSpec("q_reverse", "Q反转", 2810, 3),
    "q_forward": BitSpec("q_forward", "Q正转", 2810, 2),
    "q_work_brake": BitSpec("q_work_brake", "Q工作闸", 2810, 1),
    "q_warning": BitSpec("q_warning", "Q预警", 2810, 0),
    "heartbeat_timeout": BitSpec("heartbeat_timeout", "心跳超时", 2400, 2),
    "command_in_progress": BitSpec("command_in_progress", "命令执行中", 2400, 0),
}
