"""BMS 侧的告警逻辑（如单体低压）。"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from sqlmodel import select

from app.db.models import AlarmRecord, AlarmType, BMSData

from .alarm_publisher import build_alarm_event
from .data_service import DataService

logger = logging.getLogger(__name__)

BMS_CELL_VOLTAGE_THRESHOLD = 3.35
BMS_ALARM_DEBOUNCE_SECONDS = 120
BMS_CHARGE_COMMAND_HEX = '06 06 00 00 00 B1 48 09'


def maybe_create_bms_low_voltage_alarm(
    data_service: DataService,
    bms_data: BMSData,
    *,
    threshold: float = BMS_CELL_VOLTAGE_THRESHOLD,
    debounce_seconds: int = BMS_ALARM_DEBOUNCE_SECONDS,
) -> Optional[Dict[str, Any]]:
    """检测 BMS 单体低压并落库 + 生成 MQTT 告警事件（如需）。

    Notes:
        - 该函数仅在发现 `min(cell_voltages) < threshold` 时返回事件。
        - 为避免频繁报警，会对同一 `location`（通常为最新 RFID card_id）做简单防抖：
          `debounce_seconds` 窗口内只写入一次告警。

    Args:
        data_service (DataService): 数据服务（同一 DB Session）。
        bms_data (BMSData): 已解析/入库的 BMS 数据。
        threshold (float): 低压阈值，默认 3.35V。
        debounce_seconds (int): 防抖窗口秒数，默认 120 秒。

    Returns:
        Optional[Dict[str, Any]]: 可用于 MQTT 广播的告警事件；未触发则返回 None。
    """
    raw_cells = bms_data.cell_voltages or []
    if not isinstance(raw_cells, list) or not raw_cells:
        return None

    values: list[float] = []
    for cell in raw_cells:
        try:
            if cell is None:
                continue
            values.append(float(cell))
        except (TypeError, ValueError):
            continue

    if not values:
        return None

    min_voltage = min(values)
    if min_voltage >= float(threshold):
        return None

    location = bms_data.location or ''
    reference_time = bms_data.timestamp or datetime.now()
    window_start = reference_time - timedelta(seconds=int(debounce_seconds))

    recent_alarm = data_service.session.exec(
        select(AlarmRecord.id).where(
            AlarmRecord.sensor_key == 'cell_voltage',
            AlarmRecord.location == location,
            AlarmRecord.timestamp >= window_start,
        )
    ).first()
    if recent_alarm:
        logger.debug(
            'BMS low-voltage alarm suppressed by debounce: location=%s threshold=%.3f min=%.3f',
            location,
            threshold,
            min_voltage,
        )
        return None

    alarm = AlarmRecord(
        timestamp=reference_time,
        sensor_key='cell_voltage',
        sensor_name='BMS单体电压',
        value=float(min_voltage),
        unit='V',
        min_threshold=float(threshold),
        max_threshold=0.0,
        alarm_type=AlarmType.LOW,
        is_handled=False,
        location=location,
    )
    stored = data_service.create_alarm_record(alarm)
    logger.info(
        'BMS low-voltage alarm stored: id=%s device=%s location=%s min=%.3f threshold=%.3f',
        stored.id,
        bms_data.device_id,
        location,
        min_voltage,
        threshold,
    )

    return build_alarm_event(
        source='bms_cell_voltage',
        timestamp=stored.timestamp,
        device_id=bms_data.device_id,
        location=location,
        payload={
            'alarm_id': stored.id,
            'min_cell_voltage': float(min_voltage),
            'threshold': float(threshold),
            'cells_count': len(values),
            'charge_command_hex': BMS_CHARGE_COMMAND_HEX,
            'message': '发现单体电压过低，请及时充电',
        },
    )

