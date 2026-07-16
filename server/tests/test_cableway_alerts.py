"""Cableway PLC alarm edge-trigger tests."""

from __future__ import annotations

from datetime import datetime

from sqlmodel import select

from app.db.models import AlarmRecord
from app.services.cableway_alerts import create_cableway_alarm_events
from app.services.data_service import DataService


def test_cableway_fault_alarm_is_edge_triggered(session) -> None:
    service = DataService(session)
    timestamp = datetime(2024, 1, 1, 0, 0, 0)

    current = {
        'faults': {'gz_total_fault': True},
        'outputs': {'heartbeat_timeout': False},
        'active_faults': ['GZ总故障'],
        'plc_host': '192.168.2.1',
    }
    events = create_cableway_alarm_events(
        service,
        device_id='gw-1',
        location='CARD123',
        timestamp=timestamp,
        current_status=current,
        previous_status={'faults': {}, 'outputs': {}},
    )

    alarms = session.exec(select(AlarmRecord)).all()
    assert len(alarms) == 1
    assert alarms[0].sensor_name == 'Total fault'
    assert len(events) == 1
    assert events[0]['source'] == 'cableway_fault'

    # same state again -> no new alarm
    events2 = create_cableway_alarm_events(
        service,
        device_id='gw-1',
        location='CARD123',
        timestamp=timestamp,
        current_status=current,
        previous_status=current,
    )
    assert events2 == []


def test_cableway_output_flags_do_not_create_fault_alarms(session) -> None:
    service = DataService(session)
    timestamp = datetime(2024, 1, 1, 0, 0, 1)

    current = {
        'faults': {},
        'outputs': {'heartbeat_timeout': True, 'q_fault': True},
        'active_faults': [],
    }
    events = create_cableway_alarm_events(
        service,
        device_id='gw-1',
        location='CARD123',
        timestamp=timestamp,
        current_status=current,
        previous_status={'faults': {}, 'outputs': {}},
    )

    alarms = session.exec(select(AlarmRecord.sensor_name)).all()
    assert alarms == []
    assert events == []
