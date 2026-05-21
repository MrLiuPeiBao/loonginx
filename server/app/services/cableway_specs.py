"""Cableway PLC point table and validation helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple, Union


VW_COMMAND: int = 2404
VW_ESTOP: int = 2410
VW_HEARTBEAT: int = 2414
VW_CURRENT_TASK: int = 2432

VD_CURRENT_POSITION: int = 2244
VD_CURRENT_SPEED: int = 2248
VD_TARGET_POSITION: int = 2252

VB_FAULT_WINDOW_START: int = 2880
VB_FAULT_WINDOW_END: int = 2889
VB_STATUS_WINDOW_START: int = 2888
VB_STATUS_WINDOW_END: int = 2909

TOTAL_FAULT_KEY: str = "gz_total_fault"

HEARTBEAT_VALUES: Tuple[int, int] = (0, 5)


CONTROL_COMMANDS: Dict[int, str] = {
    101: "home",
    201: "auto_forward",
    202: "auto_reverse",
    301: "position_start",
    401: "jog_forward",
    402: "jog_reverse",
    88: "stop",
    66: "fault_reset",
    0: "no_command",
}


@dataclass(frozen=True)
class FloatStatusSpec:
    key: str
    name: str
    vd_address: int
    unit: str
    min_value: Optional[float] = None
    max_value: Optional[float] = None


@dataclass(frozen=True)
class WordStatusSpec:
    key: str
    name: str
    vw_address: int
    unit: str = ""


@dataclass(frozen=True)
class BitSpec:
    key: str
    name: str
    byte_address: int
    bit_index: int
    note: str = ""


STATUS_FLOATS: Dict[str, FloatStatusSpec] = {
    "current_position_m": FloatStatusSpec("current_position_m", "Current position", VD_CURRENT_POSITION, "m", 0.0, 999.0),
    "current_speed_mps": FloatStatusSpec("current_speed_mps", "Current speed", VD_CURRENT_SPEED, "m/s", 0.0, 0.6),
    "target_position_m": FloatStatusSpec("target_position_m", "Target position", VD_TARGET_POSITION, "m", 0.0, 999.0),
}


STATUS_WORDS: Dict[str, WordStatusSpec] = {
    "current_task_code": WordStatusSpec("current_task_code", "Current task", VW_CURRENT_TASK),
}


STATUS_BITS: Dict[str, BitSpec] = {
    TOTAL_FAULT_KEY: BitSpec(TOTAL_FAULT_KEY, "Total fault", 2889, 7),
    "zt_home_done": BitSpec("zt_home_done", "Home completed", 2909, 0),
    "zt_position_done": BitSpec("zt_position_done", "Positioning completed", 2909, 1),
}


FAULT_BITS: Dict[str, BitSpec] = {
    TOTAL_FAULT_KEY: STATUS_BITS[TOTAL_FAULT_KEY],
    "gz_position_fault": BitSpec("gz_position_fault", "Position fault", 2884, 6),
    "gz_home_fault": BitSpec("gz_home_fault", "Home fault", 2884, 5),
    "gz_over_positive_limit": BitSpec("gz_over_positive_limit", "Positive overlimit", 2884, 3),
    "gz_over_negative_limit": BitSpec("gz_over_negative_limit", "Negative overlimit", 2884, 2),
    "gz_deviation_too_large": BitSpec("gz_deviation_too_large", "Deviation too large", 2884, 1),
    "gz_setpoint_overrun": BitSpec("gz_setpoint_overrun", "Setpoint overrun", 2884, 0),
    "gz_hard_limit": BitSpec("gz_hard_limit", "Hard limit", 2883, 2),
    "gz_positive_limit_estop": BitSpec("gz_positive_limit_estop", "Positive limit estop", 2883, 1),
    "gz_negative_limit_estop": BitSpec("gz_negative_limit_estop", "Negative limit estop", 2883, 0),
    "gz_estop_fault": BitSpec("gz_estop_fault", "Estop fault", 2882, 2),
    "gz_estop_inhibit_start": BitSpec("gz_estop_inhibit_start", "Estop inhibits start", 2882, 1),
    "gz_robot_estop": BitSpec("gz_robot_estop", "Robot estop", 2882, 0),
    "gz_general_fault": BitSpec("gz_general_fault", "General fault", 2880, 5),
    "gz_counterweight_low": BitSpec("gz_counterweight_low", "Counterweight low", 2880, 4),
    "gz_counterweight_high": BitSpec("gz_counterweight_high", "Counterweight high", 2880, 3),
    "gz_overspeed": BitSpec("gz_overspeed", "Overspeed", 2880, 2),
    "gz_vfd_fault": BitSpec("gz_vfd_fault", "VFD fault", 2880, 1),
    "gz_brake_fault": BitSpec("gz_brake_fault", "Brake fault", 2880, 0),
}


FLOAT_PARAMS: Dict[str, object] = {}
WORD_PARAMS: Dict[str, object] = {}
PARAM_SPECS: Dict[str, object] = {}


def validate_control_command_code(code: int) -> int:
    value = int(code)
    if value not in CONTROL_COMMANDS:
        raise ValueError(f"Unsupported control command code: {value}")
    return value


def validate_param_updates(params: Dict[str, float]) -> Dict[str, Union[float, int]]:
    raise ValueError("set_params is no longer supported for the cableway PLC")
