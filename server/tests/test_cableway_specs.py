"""Cableway PLC specs and persistence tests."""

from __future__ import annotations

from datetime import datetime

import pytest

from app.db.models import CablewayStatus
from app.services.cableway_specs import validate_control_command_code, validate_param_updates
from app.services.data_service import DataService


def test_validate_control_command_code_accepts_known_values() -> None:
    assert validate_control_command_code(101) == 101
    assert validate_control_command_code(0) == 0


def test_validate_control_command_code_rejects_unknown_value() -> None:
    with pytest.raises(ValueError):
        validate_control_command_code(999)


def test_validate_param_updates_enforces_ranges() -> None:
    validated = validate_param_updates({"cs_auto_speed": 0.5})
    assert validated["cs_auto_speed"] == pytest.approx(0.5)

    with pytest.raises(ValueError):
        validate_param_updates({"cs_auto_speed": 0.9})


def test_cableway_status_persist_and_latest(session) -> None:
    service = DataService(session)
    timestamp = datetime(2024, 1, 1, 0, 0, 0)
    entity = CablewayStatus(
        timestamp=timestamp,
        device_id="gateway-1",
        location="unknown",
        plc_host="192.168.2.1",
        status={"faults": {"gz_total_fault": True}},
    )

    service.create_cableway_status([entity])

    latest = service.get_latest_cableway_status()
    assert latest is not None
    assert latest.device_id == "gateway-1"
    assert latest.plc_host == "192.168.2.1"
    assert latest.status and latest.status["faults"]["gz_total_fault"] is True
