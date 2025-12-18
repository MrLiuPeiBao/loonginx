"""项目配置与环境加载支持。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional, Union

from dotenv import load_dotenv
from pydantic import BaseSettings, Field
from sqlalchemy.engine import URL


def load_env(env_file: Optional[Union[str, Path]] = None) -> None:
    """加载 .env 配置文件到环境变量。"""
    if env_file is None:
        candidates = (Path('.env'),)
    else:
        path = Path(env_file)
        candidates = (path,) if path.is_file() else (Path('.env'),)

    for candidate in candidates:
        if candidate.is_file():
            load_dotenv(dotenv_path=candidate, override=False)
            break
    else:
        load_dotenv(override=False)


class Settings(BaseSettings):
    """全局配置。"""

    app_name: str = Field('sensor_server', env='APP_NAME')

    mqtt_broker: str = Field('localhost', env='MQTT_BROKER')
    mqtt_port: int = Field(1883, env='MQTT_PORT')
    mqtt_username: str = Field('', env='MQTT_USERNAME')
    mqtt_password: str = Field('', env='MQTT_PASSWORD')

    mysql_host: str = Field('localhost', env='MYSQL_HOST')
    mysql_port: int = Field(3306, env='MYSQL_PORT')
    mysql_user: str = Field('root', env='MYSQL_USER')
    mysql_password: str = Field('root', env='MYSQL_PASSWORD')
    mysql_database: str = Field('loognix', env='MYSQL_DATABASE')

    api_host: str = Field('0.0.0.0', env='API_HOST')
    api_port: int = Field(8000, env='API_PORT')

    yolo_enabled: bool = Field(False, env='YOLO_ENABLED')
    yolo_rtsp_input: str = Field('', env='YOLO_RTSP_INPUT')
    yolo_rtsp_output: str = Field('', env='YOLO_RTSP_OUTPUT')
    yolo_model_path: str = Field('yolov8n.pt', env='YOLO_MODEL_PATH')
    yolo_device_id: str = Field('camera', env='YOLO_DEVICE_ID')
    yolo_location: str = Field('rtsp', env='YOLO_LOCATION')
    yolo_screenshot_interval: float = Field(5.0, env='YOLO_SCREENSHOT_INTERVAL')
    yolo_confidence_threshold: float = Field(0.35, env='YOLO_CONFIDENCE_THRESHOLD')
    yolo_frame_width: int = Field(640, env='YOLO_FRAME_WIDTH')
    yolo_frame_height: int = Field(360, env='YOLO_FRAME_HEIGHT')
    yolo_fps: int = Field(25, env='YOLO_FPS')
    yolo_ffmpeg_enabled: bool = Field(True, env='YOLO_FFMPEG_ENABLED')
    yolo_tracker_config: str = Field('bytetrack.yaml', env='YOLO_TRACKER_CONFIG')
    yolo_person_confidence: float = Field(0.5, env='YOLO_PERSON_CONFIDENCE')
    yolo_detection_interval: float = Field(5.0, env='YOLO_DETECTION_INTERVAL')

    audio_enabled: bool = Field(False, env='AUDIO_ENABLED')
    audio_rtsp_input: str = Field('', env='AUDIO_RTSP_INPUT')
    audio_device_id: str = Field('mic', env='AUDIO_DEVICE_ID')
    audio_location: str = Field('rtsp', env='AUDIO_LOCATION')
    audio_sample_rate: int = Field(16000, env='AUDIO_SAMPLE_RATE')
    audio_window_seconds: float = Field(1.0, env='AUDIO_WINDOW_SECONDS')
    audio_threshold_centroid: float = Field(0.0, env='AUDIO_THRESHOLD_CENTROID')
    audio_threshold_bandwidth: float = Field(0.0, env='AUDIO_THRESHOLD_BANDWIDTH')
    audio_threshold_rolloff: float = Field(0.0, env='AUDIO_THRESHOLD_ROLLOFF')
    audio_threshold_flatness: float = Field(0.0, env='AUDIO_THRESHOLD_FLATNESS')
    audio_threshold_flux: float = Field(0.0, env='AUDIO_THRESHOLD_FLUX')
    audio_threshold_rms: float = Field(0.0, env='AUDIO_THRESHOLD_RMS')
    audio_debug_store_all: bool = Field(False, env='AUDIO_DEBUG_STORE_ALL')

    class Config:
        env_file = '.env'
        env_file_encoding = 'utf-8'
        case_sensitive = False

    @property
    def mysql_url(self) -> URL:
        """构建 SQLAlchemy URL。"""
        return URL.create(
            drivername='mysql+pymysql',
            username=self.mysql_user,
            password=self.mysql_password,
            host=self.mysql_host,
            port=self.mysql_port,
            database=self.mysql_database,
        )


@lru_cache()
def get_settings() -> Settings:
    """返回 Settings 单例。"""
    load_env()
    return Settings()
