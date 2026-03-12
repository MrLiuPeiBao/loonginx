from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Optional


class BaseSensor(ABC):
    """Abstract base class for all concrete sensors."""

    def __init__(self, name: str, address: int, serial_manager):
        self.name = name
        self.address = address
        self.serial_manager = serial_manager
        self.last_value: Any = None

    @abstractmethod
    def read_data(self) -> Optional[Any]:
        """Fetch raw data from the device."""

    @abstractmethod
    def parse_data(self, raw_data) -> Optional[Any]:
        """Convert raw device data to business-friendly format."""

    def get_formatted_data(self) -> Optional[dict]:
        """Return a unified data payload for the current sensor reading."""
        raw_data = self.read_data()
        if raw_data is None:
            return None

        parsed_data = self.parse_data(raw_data)
        if parsed_data is None:
            return None

        self.last_value = parsed_data
        timestamp, ts = self._get_timestamp_pair()
        return {
            'sensor_type': self.name,
            'address': self.address,
            'value': parsed_data,
            'timestamp': timestamp,
            'ts': ts,
        }

    @staticmethod
    def _get_timestamp() -> str:
        return BaseSensor._get_timestamp_pair()[0]

    @staticmethod
    def _get_timestamp_pair() -> tuple[str, float]:
        now = datetime.now().astimezone()
        return now.isoformat(), now.timestamp()
