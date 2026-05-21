"""Cableway PLC alarm generation helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlmodel import select

from app.db.models import AlarmRecord, AlarmType, CablewayStatus
from app.services.alarm_cache import set_latest_alarm
from app.services.alarm_publisher import build_alarm_event
from app.services.data_service import DataService


CABLEWAY_ALARM_SENSOR_KEY = "cableway_fault"
CABLEWAY_ALARM_SOURCE = "cableway_fault"

FAULT_NAME_BY_KEY: Dict[str, str] = {
    "gz_total_fault": "Total fault",
    "gz_position_fault": "Position fault",
    "gz_home_fault": "Home fault",
    "gz_over_positive_limit": "Positive overlimit",
    "gz_over_negative_limit": "Negative overlimit",
    "gz_deviation_too_large": "Deviation too large",
    "gz_setpoint_overrun": "Setpoint overrun",
    "gz_hard_limit": "Hard limit",
    "gz_positive_limit_estop": "Positive limit estop",
    "gz_negative_limit_estop": "Negative limit estop",
    "gz_estop_fault": "Estop fault",
    "gz_estop_inhibit_start": "Estop inhibits start",
    "gz_robot_estop": "Robot estop",
    "gz_general_fault": "General fault",
    "gz_counterweight_low": "Counterweight low",
    "gz_counterweight_high": "Counterweight high",
    "gz_overspeed": "Overspeed",
    "gz_vfd_fault": "VFD fault",
    "gz_brake_fault": "Brake fault",
}


def _to_bool_map(value: Any) -> Dict[str, bool]:
    if not isinstance(value, dict):
        return {}
    return {str(key): bool(item) for key, item in value.items()}


def _extract_active_keys(status_payload: Optional[dict]) -> Set[str]:
    if not isinstance(status_payload, dict):
        return set()
    faults = _to_bool_map(status_payload.get("faults"))
    return {key for key, enabled in faults.items() if enabled}


def _key_to_name(key: str) -> Tuple[str, str]:
    return "fault", FAULT_NAME_BY_KEY.get(key) or key


def _recently_raised(
    data_service: DataService,
    *,
    location: str,
    sensor_name: str,
    timestamp: datetime,
    window_seconds: int = 10,
) -> bool:
    row = data_service.session.exec(
        select(AlarmRecord.timestamp)
        .where(
            AlarmRecord.sensor_key == CABLEWAY_ALARM_SENSOR_KEY,
            AlarmRecord.sensor_name == sensor_name,
            AlarmRecord.location == (location or ""),
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
    prev_active = _extract_active_keys(previous_status)
    curr_active = _extract_active_keys(current_status)
    newly_active = curr_active - prev_active
    if not newly_active:
        return []

    active_faults = current_status.get("active_faults")
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
            unit="BIT",
            min_threshold=0.0,
            max_threshold=0.0,
            alarm_type=AlarmType.HIGH,
            is_handled=False,
            location=location or "",
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
                location=location or "",
                payload={
                    "alarm_id": alarm.id,
                    "signal_type": signal_type,
                    "signal_key": key,
                    "signal_name": name,
                    "active_faults": list(active_faults),
                    "plc_host": current_status.get("plc_host") or current_status.get("host"),
                },
            )
        )

    return events


def extract_status_payload(entity: Optional[CablewayStatus]) -> Optional[dict]:
    if entity is None:
        return None
    payload = getattr(entity, "status", None)
    return payload if isinstance(payload, dict) else None
