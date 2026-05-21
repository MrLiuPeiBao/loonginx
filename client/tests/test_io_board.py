from __future__ import annotations

import sys
import types
from typing import Optional


def _install_dummy_serial_modules() -> None:
    minimalmodbus_mod = types.ModuleType("minimalmodbus")

    class DummyInstrument:
        def __init__(self, *args, **kwargs):
            self.serial = types.SimpleNamespace(is_open=False)

    minimalmodbus_mod.Instrument = DummyInstrument
    sys.modules["minimalmodbus"] = minimalmodbus_mod

    serial_mod = types.ModuleType("serial")
    serial_mod.PARITY_NONE = "N"

    class DummySerial:
        pass

    serial_mod.Serial = DummySerial
    sys.modules["serial"] = serial_mod


_install_dummy_serial_modules()

from communication.io_board import IOBoard  # noqa: E402
from communication.serial_manager import SerialManager  # noqa: E402
from sensors.photoelectric_sensor import PhotoelectricSensor  # noqa: E402


class FakeSerialManager:
    def __init__(self, response: bytes = b"\x19\x05\x00\x00\xff\x00\x8f\xe2"):
        self.calls: list[tuple[bytes, bool, Optional[float]]] = []
        self.response = response

    def send_raw_command_with_options(
        self,
        command: bytes,
        *,
        wait_for_tx_complete: bool = True,
        response_timeout: Optional[float] = None,
    ) -> bytes:
        self.calls.append((command, wait_for_tx_complete, response_timeout))
        return self.response


def test_io_board_running_light_command() -> None:
    serial_manager = FakeSerialManager()
    io_board = IOBoard(serial_manager)

    assert io_board.set_output("running", True)
    assert serial_manager.calls == [
        (bytes.fromhex("19 05 00 00 FF 00 8F E2"), True, None),
    ]


def test_io_board_can_skip_tx_drain_for_startup() -> None:
    serial_manager = FakeSerialManager()
    io_board = IOBoard(serial_manager)

    assert io_board.set_output(
        "running",
        True,
        wait_for_tx_complete=False,
        response_timeout=0.2,
    )
    assert serial_manager.calls == [
        (bytes.fromhex("19 05 00 00 FF 00 8F E2"), False, 0.2),
    ]


def test_photoelectric_one_side_obstacle() -> None:
    sensor = PhotoelectricSensor(FakeSerialManager(), {"address": 25})

    parsed = sensor.parse_data(bytes.fromhex("19 02 01 01 66 E8"))

    assert parsed is not None
    assert parsed["value"] == 1
    assert parsed["obstacle_detected"] is True
    assert parsed["front_obstacle"] is True
    assert parsed["rear_obstacle"] is False
    assert parsed["left_obstacle"] is True
    assert parsed["right_obstacle"] is False


def test_photoelectric_rear_obstacle() -> None:
    sensor = PhotoelectricSensor(FakeSerialManager(), {"address": 25})

    parsed = sensor.parse_data(bytes.fromhex("19 02 01 02 26 E9"))

    assert parsed is not None
    assert parsed["value"] == 1
    assert parsed["obstacle_detected"] is True
    assert parsed["front_obstacle"] is False
    assert parsed["rear_obstacle"] is True
    assert parsed["left_obstacle"] is False
    assert parsed["right_obstacle"] is True


def test_photoelectric_two_side_obstacle() -> None:
    sensor = PhotoelectricSensor(FakeSerialManager(), {"address": 25})

    parsed = sensor.parse_data(bytes.fromhex("19 02 01 03 E7 29"))

    assert parsed is not None
    assert parsed["value"] == 2
    assert parsed["obstacle_detected"] is True
    assert parsed["front_obstacle"] is True
    assert parsed["rear_obstacle"] is True
    assert parsed["left_obstacle"] is True
    assert parsed["right_obstacle"] is True


def test_photoelectric_no_obstacle() -> None:
    sensor = PhotoelectricSensor(FakeSerialManager(), {"address": 25})

    parsed = sensor.parse_data(bytes.fromhex("19 02 01 00 A7 28"))

    assert parsed is not None
    assert parsed["value"] == 0
    assert parsed["obstacle_detected"] is False
    assert parsed["front_obstacle"] is False
    assert parsed["rear_obstacle"] is False
    assert parsed["left_obstacle"] is False
    assert parsed["right_obstacle"] is False


def test_photoelectric_uses_configured_response_timeout() -> None:
    serial_manager = FakeSerialManager()
    sensor = PhotoelectricSensor(serial_manager, {"address": 25, "response_timeout": 0.25})

    sensor.read_data()

    assert serial_manager.calls == [
        (bytes.fromhex("19 02 00 00 00 02 FA 13"), True, 0.25),
    ]


def test_estimate_tx_airtime_seconds() -> None:
    serial_port = types.SimpleNamespace(baudrate=9600, bytesize=8, parity="N", stopbits=1)

    airtime = SerialManager._estimate_tx_airtime_seconds(serial_port, 8)

    assert 0.008 < airtime < 0.009
