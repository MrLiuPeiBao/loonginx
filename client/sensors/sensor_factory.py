import logging
from typing import Optional

try:
    from .bms_sensors import BMSSensor
except Exception:  # pragma: no cover - optional in trimmed local workspace
    BMSSensor = None

try:
    from .gas_sensors import CH4Sensor, COSensor, H2SSensor, O2Sensor, SmokeSensor
except Exception:  # pragma: no cover - optional in trimmed local workspace
    CH4Sensor = COSensor = H2SSensor = O2Sensor = SmokeSensor = None

from .photoelectric_sensor import PhotoelectricSensor

try:
    from .temperature_sensor import HumiditySensor, PressureSensor, SharedSmokeSensor, TemperatureSensor
except Exception:  # pragma: no cover - optional in trimmed local workspace
    HumiditySensor = PressureSensor = SharedSmokeSensor = TemperatureSensor = None


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
        'smoke': SharedSmokeSensor or SmokeSensor,
        'photoelectric': PhotoelectricSensor,
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
