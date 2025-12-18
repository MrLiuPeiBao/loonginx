from __future__ import annotations

from .base_sensor import BaseSensor
from utils.data_parser import DataParser


class BMSSensor(BaseSensor):
    """BMS aggregation sensor that fetches multiple registers."""

    def __init__(self, serial_manager, config: dict):
        super().__init__('bms', config['address'], serial_manager)
        self.registers = config['registers']
        self.function_code = config.get('function_code', 3)
        self.max_retries = config.get('max_retries', 3)
        self.response_delay = config.get('response_delay', 0.02)
        self.response_timeout = config.get('response_timeout', 1.0)

    def read_all_data(self):
        data = {}
        for key, register_cfg in self.registers.items():
            register_addr = register_cfg['addr']
            register_count = register_cfg.get('registers', 1)
            fc = register_cfg.get('function_code', self.function_code)
            retries = register_cfg.get('retries', self.max_retries)
            response_delay = register_cfg.get('response_delay', self.response_delay)
            response_timeout = register_cfg.get('response_timeout', self.response_timeout)
            multiplier = register_cfg.get('multiplier', 1)
            offset = register_cfg.get('offset', 0.0)
            precision = register_cfg.get('precision', 3)

            raw_data = self.serial_manager.read_registers_direct(
                self.address,
                register_addr,
                register_count,
                function_code=fc,
                retries=retries,
                response_delay=response_delay,
                timeout=response_timeout,
            )
            if not raw_data:
                data[key] = None
                continue

            if register_count == 1:
                register_bytes = raw_data[0].to_bytes(2, byteorder='big')
                data[key] = DataParser.parse_bms_data(
                    register_bytes,
                    multiplier,
                    offset,
                    precision,
                )
            else:
                parsed_values = []
                for value in raw_data:
                    parsed = DataParser.parse_bms_data(
                        value.to_bytes(2, byteorder='big'),
                        multiplier,
                        offset,
                        precision,
                    )
                    parsed_values.append(parsed)
                data[key] = parsed_values

        return {
            'sensor_type': self.name,
            'address': self.address,
            'timestamp': self._get_timestamp(),
            'data': data,
        }

    def read_data(self):
        return self.read_all_data()

    def parse_data(self, raw_data):
        return raw_data
