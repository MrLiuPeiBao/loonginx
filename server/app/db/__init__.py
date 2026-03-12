"""数据库模块初始化。"""

from .models import (  # noqa: F401
    AlarmRecord,
    AudioData,
    CommandLog,
    ImageData,
    MetalAnomaly,
    RFIDData,
    SensorConfig,
    RuntimeConfig,
    SensorData,
    SensorTypeEnum,
    BMSData,
)


def __getattr__(name: str):
    if name in {"engine", "get_session"}:
        from . import session as _session
        return getattr(_session, name)
    raise AttributeError(f"module 'app.db' has no attribute {name!r}")
