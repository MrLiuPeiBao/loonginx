from __future__ import annotations

from typing import Dict, Optional

from app.db.models import BMSData
from app.services.alarm_publisher import build_alarm_event


def maybe_create_bms_low_voltage_alarm(service, entity: BMSData) -> Optional[Dict[str, object]]:
    """Create a BMS low-voltage alarm event when any cell is below threshold."""
    cells = entity.cell_voltages or []
    values = [float(value) for value in cells if value is not None]
    if not values:
        return None
    min_voltage = min(values)
    threshold = 3.35
    if min_voltage >= threshold:
        return None
    return build_alarm_event(
        source='bms_low_voltage',
        timestamp=entity.timestamp,
        device_id=entity.device_id,
        location=entity.location,
        payload={
            'min_voltage': min_voltage,
            'threshold': threshold,
        },
    )

