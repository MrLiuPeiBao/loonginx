import logging
import struct
from typing import Optional


class DataParser:
    """Helper functions for decoding Modbus register payloads."""

    @staticmethod
    def parse_dcba(data_bytes: bytes) -> Optional[float]:
        """Decode a 32-bit float stored as DCBA."""
        try:
            if len(data_bytes) != 4:
                raise ValueError(f'dcba expects 4 bytes, received {len(data_bytes)} bytes')

            converted_bytes = bytes(reversed(data_bytes))
            value = struct.unpack('>f', converted_bytes)[0]
            return round(value, 2)
        except Exception as exc:
            logging.error("Failed to parse DCBA data=%s error=%s", data_bytes.hex(), exc)
            return None

    parse_DCBA = parse_dcba  # backwards compatibility

    @staticmethod
    def parse_raw(data_bytes: bytes) -> Optional[int]:
        """Decode an unsigned 16-bit integer."""
        try:
            if len(data_bytes) != 2:
                raise ValueError(f'raw expects 2 bytes, received {len(data_bytes)} bytes')
            return int.from_bytes(data_bytes, byteorder='big', signed=False)
        except Exception as exc:
            logging.error("Failed to parse raw 16-bit data=%s error=%s", data_bytes.hex(), exc)
            return None

    @staticmethod
    def parse_o2(data_bytes: bytes) -> Optional[float]:
        """O2 sensor payload scaled by 10."""
        raw_value = DataParser.parse_raw(data_bytes)
        return round(raw_value / 10.0, 1) if raw_value is not None else None

    @staticmethod
    def parse_smoke(data_bytes: bytes) -> Optional[float]:
        """Smoke sensor payload scaled by 10."""
        raw_value = DataParser.parse_raw(data_bytes)
        return round(raw_value / 10.0, 1) if raw_value is not None else None

    @staticmethod
    def parse_bms_data(
        data_bytes: bytes,
        multiplier: float = 1.0,
        offset: float = 0.0,
        precision: int = 3,
    ) -> Optional[float]:
        """Scale BMS register value with optional offset and precision controls."""
        raw_value = DataParser.parse_raw(data_bytes)
        if raw_value is None:
            return None

        adjusted = (raw_value + offset) * multiplier
        return round(adjusted, precision)
