from __future__ import annotations

import logging

from .base_sensor import BaseSensor
from utils.data_parser import DataParser


class GasSensor(BaseSensor):
    """Generic gas and smoke sensor parsing register payloads."""

    def __init__(self, name: str, serial_manager, config: dict):
        super().__init__(name, config['address'], serial_manager)
        self.parse_type = config.get('parse_type', 'raw').lower()
        self.register_addr = config.get('register_addr', 0)
        self.register_count = config.get('registers', 1)
        self.function_code = config.get('function_code', 3)
        self.direct_retries = config.get('direct_retries')
        self.direct_response_delay = config.get('direct_response_delay')
        self.direct_timeout = config.get('direct_timeout')
        self.max_attempts = config.get('max_attempts')

    def read_data(self):
        return self.serial_manager.read_registers_direct(
            self.address,
            self.register_addr,
            self.register_count,
            function_code=self.function_code,
            retries=self.direct_retries,
            response_delay=self.direct_response_delay,
            timeout=self.direct_timeout,
        )

    def parse_data(self, raw_data):
        if not raw_data:
            return None

        data_bytes = b''.join(value.to_bytes(2, byteorder='big') for value in raw_data)
        word = data_bytes[:2]

        if self.parse_type == 'raw':
            return DataParser.parse_raw(word)
        if self.parse_type == 'o2':
            return DataParser.parse_o2(word)
        if self.parse_type == 'smoke':
            return DataParser.parse_smoke(word)
        if self.parse_type == 'dcba':
            return DataParser.parse_dcba(data_bytes)

        logging.warning("Unknown parse type parse_type=%s", self.parse_type)
        return None


class COSensor(GasSensor):
    def __init__(self, serial_manager, config: dict):
        super().__init__('co', serial_manager, config)


class H2SSensor(GasSensor):
    def __init__(self, serial_manager, config: dict):
        super().__init__('h2s', serial_manager, config)


class O2Sensor(GasSensor):
    def __init__(self, serial_manager, config: dict):
        super().__init__('o2', serial_manager, config)


class CH4Sensor(GasSensor):
    def __init__(self, serial_manager, config: dict):
        super().__init__('ch4', serial_manager, config)


class SmokeSensor(GasSensor):
    def __init__(self, serial_manager, config: dict):
        super().__init__('smoke', serial_manager, config)
