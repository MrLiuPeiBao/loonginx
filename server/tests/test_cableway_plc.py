from __future__ import annotations

import struct
from types import SimpleNamespace

from app.services import cableway_plc


def _encode_float32(value: float) -> list[int]:
    raw = struct.pack(">f", float(value))
    return [
        struct.unpack(">H", raw[0:2])[0],
        struct.unpack(">H", raw[2:4])[0],
    ]


def _make_settings(**overrides):
    base = {
        "plc_direct_enabled": True,
        "plc_host": "127.0.0.1",
        "plc_port": 502,
        "plc_unit_id": 1,
        "plc_timeout": 0.5,
        "plc_connect_timeout": 1.0,
        "plc_status_poll_interval": 0.2,
        "plc_heartbeat_interval": 1.0,
        "plc_command_pulse_seconds": 0.01,
        "plc_even_byte_is_high": True,
        "plc_float_word_order": "big",
        "plc_float_byte_order": "big",
        "plc_device_id": "server-plc",
        "plc_location": "server",
        "app_name": "sensor_server",
        "command_timeout_seconds": 2,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _make_status_payload() -> dict:
    return {
        "timestamp": "2026-04-10T00:00:00+08:00",
        "ts": 1.0,
        "plc_host": "127.0.0.1",
        "current_position_m": 1.5,
        "current_speed_mps": 0.2,
        "target_position_m": 5.0,
        "current_task_code": 101,
        "total_fault": False,
        "home_completed": False,
        "positioning_completed": False,
        "faults": {"gz_total_fault": False},
        "outputs": {"zt_home_done": False, "zt_position_done": False},
        "active_faults": [],
        "fault_query_performed": False,
        "fault_query_error": None,
    }


def test_read_status_only_queries_fault_details_when_total_fault_is_set(monkeypatch):
    motion_registers = _encode_float32(1.5) + _encode_float32(0.25) + _encode_float32(12.0)
    task_registers = [401]
    status_without_fault = [0] * 11
    status_without_fault[-1] = 0x0001
    status_with_fault = [0] * 11
    status_with_fault[0] = 0x0080
    status_with_fault[-1] = 0x0003
    fault_registers = [0x0100, 0, 0, 0, 0x0080]

    class FakeClient:
        def __init__(self, config):
            self.config = config
            self.reads: list[tuple[int, int]] = []

        def set_trace_sink(self, sink):
            self.trace_sink = sink

        def connect(self):
            return None

        def close(self):
            return None

        def write_single_register(self, address, value):
            return None

        def read_holding_registers(self, address, count):
            self.reads.append((address, count))
            if (address, count) == (1122, 6):
                return list(motion_registers)
            if (address, count) == (1216, 1):
                return list(task_registers)
            if (address, count) == (1444, 11):
                if len([item for item in self.reads if item == (1444, 11)]) == 1:
                    return list(status_without_fault)
                return list(status_with_fault)
            if (address, count) == (1440, 5):
                return list(fault_registers)
            raise AssertionError(f"unexpected read {(address, count)}")

    monkeypatch.setattr(cableway_plc, "ModbusTCPClient", FakeClient)

    plc = cableway_plc.CablewayPLC(cableway_plc.CablewayPLCConfig())

    status = plc.read_status()
    assert status["total_fault"] is False
    assert status["fault_query_performed"] is False
    assert (1440, 5) not in plc._client.reads

    status = plc.read_status()
    assert status["total_fault"] is True
    assert status["fault_query_performed"] is True
    assert status["faults"]["gz_brake_fault"] is True
    assert "Total fault" in status["active_faults"]
    assert "Brake fault" in status["active_faults"]
    assert (1440, 5) in plc._client.reads
