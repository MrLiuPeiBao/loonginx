from __future__ import annotations

from .base_sensor import BaseSensor
from utils.data_parser import DataParser


class _EnvironmentSensor(BaseSensor):
    """Generic environment sensor handling DCBA-formatted floats."""

    def __init__(self, name: str, serial_manager, config: dict):
        super().__init__(name, config['address'], serial_manager)
        self.register_addr = config.get('register_addr', 0)
        self.register_count = config.get('registers', 1)
        self.function_code = config.get('function_code', 3)

    def read_data(self):
        return self.serial_manager.read_registers(
            self.address,
            self.register_addr,
            self.register_count,
            function_code=self.function_code,
        )

    def parse_data(self, raw_data):
        if not raw_data or len(raw_data) != self.register_count:
            return None

        data_bytes = b''.join(value.to_bytes(2, byteorder='big') for value in raw_data)
        return DataParser.parse_dcba(data_bytes)


class TemperatureSensor(_EnvironmentSensor):
    def __init__(self, serial_manager, config: dict):
        super().__init__('temperature', serial_manager, config)


class HumiditySensor(_EnvironmentSensor):
    def __init__(self, serial_manager, config: dict):
        super().__init__('humidity', serial_manager, config)


class PressureSensor(_EnvironmentSensor):
    def __init__(self, serial_manager, config: dict):
        super().__init__('pressure', serial_manager, config)
