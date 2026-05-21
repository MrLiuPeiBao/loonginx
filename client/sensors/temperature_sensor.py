from __future__ import annotations

from typing import Optional

from .base_sensor import BaseSensor
from utils.data_parser import DataParser


class _EnvironmentSensor(BaseSensor):
    """Environment sensor supporting direct and standard reads."""

    def __init__(self, name: str, serial_manager, config: dict):
        super().__init__(name, config["address"], serial_manager)
        self.register_addr = config.get("register_addr", 0)
        self.register_count = config.get("registers", 1)
        self.function_code = config.get("function_code", 3)
        self.parse_type = str(config.get("parse_type", "dcba")).lower()
        self.read_mode = str(config.get("read_mode", "standard")).lower()
        self.direct_retries = config.get("direct_retries")
        self.direct_response_delay = config.get("direct_response_delay")
        self.direct_timeout = config.get("direct_timeout")
        self.max_attempts = config.get("max_attempts")

    def read_data(self):
        if self.read_mode == "direct":
            return self.serial_manager.read_registers_direct(
                self.address,
                self.register_addr,
                self.register_count,
                function_code=self.function_code,
                retries=self.direct_retries,
                response_delay=self.direct_response_delay,
                timeout=self.direct_timeout,
            )

        return self.serial_manager.read_registers(
            self.address,
            self.register_addr,
            self.register_count,
            function_code=self.function_code,
        )

    def parse_data(self, raw_data):
        if not raw_data or len(raw_data) != self.register_count:
            return None

        data_bytes = b"".join(value.to_bytes(2, byteorder="big") for value in raw_data)
        if self.parse_type == "dcba":
            return DataParser.parse_dcba(data_bytes)
        if self.parse_type == "shangluo_temperature":
            return DataParser.parse_shangluo_temperature(data_bytes[:2])
        if self.parse_type == "shangluo_humidity":
            return DataParser.parse_shangluo_humidity(data_bytes[:2])
        if self.parse_type == "shangluo_smoke":
            return DataParser.parse_shangluo_smoke(data_bytes[:2])
        return None


class TemperatureSensor(_EnvironmentSensor):
    def __init__(self, serial_manager, config: dict):
        super().__init__("temperature", serial_manager, config)


class HumiditySensor(_EnvironmentSensor):
    def __init__(self, serial_manager, config: dict):
        super().__init__("humidity", serial_manager, config)


class PressureSensor(_EnvironmentSensor):
    def __init__(self, serial_manager, config: dict):
        super().__init__("pressure", serial_manager, config)


class SharedSmokeSensor(_EnvironmentSensor):
    def __init__(self, serial_manager, config: dict):
        super().__init__("smoke", serial_manager, config)
