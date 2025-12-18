import logging
from typing import Optional

from .bms_sensors import BMSSensor
from .gas_sensors import CH4Sensor, COSensor, H2SSensor, O2Sensor, SmokeSensor
from .temperature_sensor import HumiditySensor, PressureSensor, TemperatureSensor


class SensorFactory:
    """Create sensor instances based on configuration."""

    SENSOR_TYPES = {
        'temperature': TemperatureSensor,
        'humidity': HumiditySensor,
        'pressure': PressureSensor,
        'co': COSensor,
        'h2s': H2SSensor,
        'o2': O2Sensor,
        'ch4': CH4Sensor,
        'smoke': SmokeSensor,
        'bms': BMSSensor,
    }

    @classmethod
    def create_sensor(cls, sensor_type: str, serial_manager, config: Optional[dict] = None):
        sensor_cls = cls.SENSOR_TYPES.get(sensor_type)
        if sensor_cls is None:
            logging.warning("Unknown sensor type: %s", sensor_type)
            return None

        if config is None:
            logging.error("Missing config for sensor type=%s", sensor_type)
            return None

        try:
            return sensor_cls(serial_manager, config)
        except Exception as exc:
            logging.error("Failed to create sensor type=%s error=%s", sensor_type, exc)
            return None
