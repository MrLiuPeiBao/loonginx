try:
    from .bms_sensors import BMSSensor
except Exception:  # pragma: no cover - optional in trimmed local workspace
    BMSSensor = None

try:
    from .gas_sensors import CH4Sensor, COSensor, H2SSensor, O2Sensor, SmokeSensor
except Exception:  # pragma: no cover - optional in trimmed local workspace
    CH4Sensor = COSensor = H2SSensor = O2Sensor = SmokeSensor = None

try:
    from .photoelectric_sensor import PhotoelectricSensor
except Exception:  # pragma: no cover - optional in trimmed local workspace
    PhotoelectricSensor = None

from .sensor_factory import SensorFactory

try:
    from .temperature_sensor import HumiditySensor, PressureSensor, TemperatureSensor
except Exception:  # pragma: no cover - optional in trimmed local workspace
    HumiditySensor = PressureSensor = TemperatureSensor = None

__all__ = [
    'SensorFactory',
    'TemperatureSensor',
    'HumiditySensor',
    'PressureSensor',
    'COSensor',
    'H2SSensor',
    'O2Sensor',
    'CH4Sensor',
    'SmokeSensor',
    'PhotoelectricSensor',
    'BMSSensor',
]
