"""Pydantic/SQLModel 架构声明。"""

from .sensor import SensorDataCreate, SensorDataRead  # noqa: F401
from .bms import BMSDataCreate, BMSDataRead  # noqa: F401
from .rfid import RFIDDataCreate, RFIDDataRead  # noqa: F401
from .config import SensorConfigCreate, SensorConfigRead  # noqa: F401
from .command import CommandLogRead, CommandRequest  # noqa: F401
from .alarm import AlarmRecordRead  # noqa: F401
from .image import ImageDataCreate, ImageDataRead  # noqa: F401
from .audio import AudioDataCreate, AudioDataRead  # noqa: F401
from .metal import MetalAnomalyCreate, MetalAnomalyRead  # noqa: F401
