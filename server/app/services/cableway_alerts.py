"""索道 PLC 状态告警生成逻辑。

目标：
- 将 `cableway/status` 上报的“故障/关键状态”纳入统一告警体系：
  - 落库到 `alarm_records`
  - 同步发布到 MQTT `sensors/alarms`

设计原则：
- 边沿触发：仅在“从无到有”的新故障/异常出现时生成告警，避免 0.5s 轮询造成告警风暴。
- 规则清晰：规则来源于点表（`机器人索道通信格式与点表.xlsx`）中的“故障报警”和部分关键输出。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlmodel import select

from app.db.models import AlarmRecord, AlarmType, CablewayStatus
from app.services.alarm_publisher import build_alarm_event
from app.services.alarm_cache import set_latest_alarm
from app.services.data_service import DataService


# 统一写入 alarm_records 的 sensor_key（长度需 <= 20）
CABLEWAY_ALARM_SENSOR_KEY = 'cableway_fault'

# 统一发布到 sensors/alarms 的 source
CABLEWAY_ALARM_SOURCE = 'cableway_fault'


# 点表：故障报警（GZ*）
FAULT_NAME_BY_KEY: Dict[str, str] = {
    'gz_total_fault': 'GZ总故障',
    'gz_position_fault': 'GZ位置故障',
    'gz_home_fault': 'GZ原点故障',
    'gz_over_positive_limit': 'GZ位置超正限',
    'gz_over_negative_limit': 'GZ位置超负限',
    'gz_deviation_too_large': 'GZ偏差过大',
    'gz_setpoint_overrun': 'GZ给定超限',
    'gz_hard_limit': 'GZ硬限位',
    'gz_positive_limit_estop': 'GZ正限急停',
    'gz_negative_limit_estop': 'GZ负限急停',
    'gz_estop_fault': 'GZ急停故障',
    'gz_estop_inhibit_start': 'GZ急停禁启',
    'gz_robot_estop': 'GZ机器人急停',
    'gz_general_fault': 'GZ普通故障',
    'gz_counterweight_low': 'GZ重锤下限',
    'gz_counterweight_high': 'GZ重锤上限',
    'gz_overspeed': 'GZ超速',
    'gz_vfd_fault': 'GZ变频故障',
    'gz_brake_fault': 'GZ报闸故障',
}


# 点表：输出信号监视（选择对系统健康最关键的信号纳入告警）
OUTPUT_ALARM_NAME_BY_KEY: Dict[str, str] = {
    'heartbeat_timeout': '心跳超时',
    'q_fault': 'Q故障',
}


def _to_bool_map(value: Any) -> Dict[str, bool]:
    if not isinstance(value, dict):
        return {}
    out: Dict[str, bool] = {}
    for key, item in value.items():
        out[str(key)] = bool(item)
    return out


def _extract_active_keys(status_payload: Optional[dict]) -> Set[str]:
    """从 status 快照提取“应触发告警”的活跃键集合。"""
    if not isinstance(status_payload, dict):
        return set()
    faults = _to_bool_map(status_payload.get('faults'))
    outputs = _to_bool_map(status_payload.get('outputs'))

    active: Set[str] = {key for key, enabled in faults.items() if enabled}
    for key, name in OUTPUT_ALARM_NAME_BY_KEY.items():
        if outputs.get(key):
            active.add(key)
    return active


def _key_to_name(key: str) -> Tuple[str, str]:
    if key in OUTPUT_ALARM_NAME_BY_KEY:
        return 'output', OUTPUT_ALARM_NAME_BY_KEY[key]
    return 'fault', FAULT_NAME_BY_KEY.get(key) or key


def _recently_raised(
    data_service: DataService,
    *,
    location: str,
    sensor_name: str,
    timestamp: datetime,
    window_seconds: int = 10,
) -> bool:
    """对同一 location + sensor_name 做短窗口抑制，避免快速抖动造成重复告警。"""
    row = data_service.session.exec(
        select(AlarmRecord.timestamp)
        .where(
            AlarmRecord.sensor_key == CABLEWAY_ALARM_SENSOR_KEY,
            AlarmRecord.sensor_name == sensor_name,
            AlarmRecord.location == (location or ''),
        )
        .order_by(AlarmRecord.timestamp.desc())
        .limit(1)
    ).first()
    if not row:
        return False
    try:
        delta_seconds = (timestamp - row).total_seconds()
        return 0 <= delta_seconds < float(window_seconds)
    except Exception:
        return False


def create_cableway_alarm_events(
    data_service: DataService,
    *,
    device_id: str,
    location: str,
    timestamp: datetime,
    current_status: dict,
    previous_status: Optional[dict],
) -> List[Dict[str, Any]]:
    """为新出现的故障/关键状态创建 DB 告警，并返回 MQTT 事件列表。"""
    prev_active = _extract_active_keys(previous_status)
    curr_active = _extract_active_keys(current_status)
    newly_active = curr_active - prev_active
    if not newly_active:
        return []

    outputs = _to_bool_map(current_status.get('outputs'))
    active_faults = current_status.get('active_faults')
    if not isinstance(active_faults, list):
        active_faults = []

    created: List[tuple[str, AlarmRecord]] = []
    for key in sorted(newly_active):
        signal_type, name = _key_to_name(key)
        if _recently_raised(
            data_service,
            location=location,
            sensor_name=name,
            timestamp=timestamp,
            window_seconds=10,
        ):
            continue

        alarm = AlarmRecord(
            timestamp=timestamp,
            sensor_key=CABLEWAY_ALARM_SENSOR_KEY,
            sensor_name=name,
            value=1.0,
            unit='BIT',
            min_threshold=0.0,
            max_threshold=0.0,
            alarm_type=AlarmType.HIGH,
            is_handled=False,
            location=location or '',
        )
        data_service.session.add(alarm)
        created.append((key, alarm))

    if not created:
        return []

    data_service.session.flush()
    latest_alarm = max(
        (alarm for _, alarm in created),
        key=lambda alarm: (alarm.timestamp, alarm.id or 0),
    )
    set_latest_alarm(latest_alarm)
    events: List[Dict[str, Any]] = []
    for key, alarm in created:
        signal_type, name = _key_to_name(key)
        events.append(
            build_alarm_event(
                source=CABLEWAY_ALARM_SOURCE,
                timestamp=alarm.timestamp,
                device_id=device_id,
                location=location or '',
                payload={
                    'alarm_id': alarm.id,
                    'signal_type': signal_type,
                    'signal_key': key,
                    'signal_name': name,
                    'active_faults': list(active_faults),
                    'outputs': dict(outputs),
                    'plc_host': current_status.get('plc_host') or current_status.get('host'),
                },
            )
        )

    return events


def extract_status_payload(entity: Optional[CablewayStatus]) -> Optional[dict]:
    if entity is None:
        return None
    payload = getattr(entity, 'status', None)
    return payload if isinstance(payload, dict) else None
