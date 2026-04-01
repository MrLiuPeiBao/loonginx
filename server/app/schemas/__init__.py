"""Pydantic/SQLModel 架构声明。"""

from .sensor import SensorDataCreate, SensorDataPage, SensorDataRead  # noqa: F401
from .bms import BMSDataCreate, BMSDataRead  # noqa: F401
from .rfid import RFIDDataCreate, RFIDDataRead  # noqa: F401
from .config import SensorConfigCreate, SensorConfigRead  # noqa: F401
from .command import CommandLogRead, CommandRequest  # noqa: F401
from .alarm import AlarmRecordPage, AlarmRecordRead  # noqa: F401
from .image import ImageDataCreate, ImageDataPage, ImageDataRead  # noqa: F401
from .audio import AudioDataCreate, AudioDataPage, AudioDataRead  # noqa: F401
from .metal import MetalAnomalyCreate, MetalAnomalyPage, MetalAnomalyRead  # noqa: F401
