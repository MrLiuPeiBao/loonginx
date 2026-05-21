from __future__ import annotations

from sensors.gas_sensors import COSensor
from sensors.temperature_sensor import HumiditySensor, SharedSmokeSensor, TemperatureSensor
from utils.data_parser import DataParser


class FakeSerialManager:
    def __init__(self, values):
        self.values = values
        self.calls = []

    def read_registers(self, address, register, count, *, function_code=3):
        self.calls.append(("standard", address, register, count, function_code))
        return self.values

    def read_registers_direct(
        self,
        address,
        register,
        count,
        *,
        function_code=3,
        retries=None,
        response_delay=None,
        timeout=None,
    ):
        self.calls.append(("direct", address, register, count, function_code, retries, response_delay, timeout))
        return self.values


def test_shangluo_temperature_formula_from_manual() -> None:
    assert DataParser.parse_shangluo_temperature(bytes.fromhex("12 c0")) == 28.0


def test_shangluo_humidity_formula_from_manual() -> None:
    assert DataParser.parse_shangluo_humidity(bytes.fromhex("09 d0")) == 25.12


def test_shangluo_smoke_formula_raw_scaled() -> None:
    assert DataParser.parse_shangluo_smoke(bytes.fromhex("01 f4")) == 5.0


def test_environment_sensors_read_individually() -> None:
    serial_manager = FakeSerialManager([0x12C0])
    temp = TemperatureSensor(
        serial_manager,
        {
            "address": 15,
            "register_addr": 1,
            "registers": 1,
            "read_mode": "direct",
            "direct_retries": 1,
            "direct_timeout": 1.0,
            "parse_type": "shangluo_temperature",
        },
    )
    temp_data = temp.get_formatted_data()

    serial_manager.values = [0x09D0]
    humi = HumiditySensor(
        serial_manager,
        {
            "address": 15,
            "register_addr": 2,
            "registers": 1,
            "read_mode": "direct",
            "direct_retries": 1,
            "direct_timeout": 1.0,
            "parse_type": "shangluo_humidity",
        },
    )
    humi_data = humi.get_formatted_data()

    serial_manager.values = [0x01F4]
    smoke = SharedSmokeSensor(
        serial_manager,
        {
            "address": 15,
            "register_addr": 11,
            "registers": 1,
            "read_mode": "direct",
            "direct_retries": 1,
            "direct_timeout": 1.0,
            "parse_type": "shangluo_smoke",
        },
    )
    smoke_data = smoke.get_formatted_data()

    assert temp_data["value"] == 28.0
    assert humi_data["value"] == 25.12
    assert smoke_data["value"] == 5.0
    assert serial_manager.calls == [
        ("direct", 15, 1, 1, 3, 1, None, 1.0),
        ("direct", 15, 2, 1, 3, 1, None, 1.0),
        ("direct", 15, 11, 1, 3, 1, None, 1.0),
    ]
def test_gas_sensor_uses_configured_direct_budget() -> None:
    serial_manager = FakeSerialManager([0x0000, 0x0000])
    sensor = COSensor(
        serial_manager,
        {
            "address": 1,
            "register_addr": 0x65,
            "registers": 2,
            "parse_type": "raw",
            "function_code": 3,
            "direct_retries": 1,
            "direct_response_delay": 0.05,
            "direct_timeout": 0.75,
        },
    )

    sensor.get_formatted_data()

    assert serial_manager.calls == [
        ("direct", 1, 101, 2, 3, 1, 0.05, 0.75),
    ]


def test_gas_sensor_can_use_more_stable_budget() -> None:
    serial_manager = FakeSerialManager([0x0000, 0x0000])
    sensor = COSensor(
        serial_manager,
        {
            "address": 1,
            "register_addr": 0x65,
            "registers": 2,
            "parse_type": "raw",
            "function_code": 3,
            "direct_retries": 2,
            "direct_response_delay": 0.08,
            "direct_timeout": 1.2,
        },
    )

    sensor.get_formatted_data()

    assert serial_manager.calls == [
        ("direct", 1, 101, 2, 3, 2, 0.08, 1.2),
    ]
