"""项目配置与环境加载支持。"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional, Union

from dotenv import load_dotenv
try:
    from pydantic import BaseSettings, Field, validator
except Exception:  # pragma: no cover - 兼容 pydantic v2
    from pydantic.v1 import BaseSettings, Field, validator
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
    cors_allow_origins: list[str] = Field(
        default_factory=lambda: [
            'http://192.168.31.3:8080',
            'http://localhost:8080',
            'http://127.0.0.1:8080',
            'http://localhost:3000',
            'http://127.0.0.1:3000',
            'http://localhost:5173',
            'http://127.0.0.1:5173',
        ],
        env='CORS_ALLOW_ORIGINS',
    )

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

    prefer_payload_device_id: bool = Field(False, env='PREFER_PAYLOAD_DEVICE_ID')
    command_timeout_seconds: int = Field(15, env='COMMAND_TIMEOUT_SECONDS')

    yolo_enabled: bool = Field(False, env='YOLO_ENABLED')
    yolo_run_mode: str = Field('thread', env='YOLO_RUN_MODE')
    yolo_rtsp_input: str = Field('', env='YOLO_RTSP_INPUT')
    yolo_rtsp_output: str = Field('', env='YOLO_RTSP_OUTPUT')
    yolo_rtsp_backend: str = Field('gstreamer', env='YOLO_RTSP_BACKEND')
    yolo_rtsp_capture_options: str = Field('', env='YOLO_RTSP_CAPTURE_OPTIONS')
    yolo_capture_buffer_size: int = Field(1, env='YOLO_CAPTURE_BUFFER_SIZE')
    yolo_drop_frames: bool = Field(True, env='YOLO_DROP_FRAMES')
    yolo_drop_max_frames: int = Field(10, env='YOLO_DROP_MAX_FRAMES')
    yolo_rtsp_low_latency: bool = Field(True, env='YOLO_RTSP_LOW_LATENCY')
    yolo_gst_encoder: str = Field('x264enc', env='YOLO_GST_ENCODER')
    yolo_gst_encoder_props: str = Field('', env='YOLO_GST_ENCODER_PROPS')
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
    audio_run_mode: str = Field('thread', env='AUDIO_RUN_MODE')
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

    media_storage_mode: str = Field('database', env='MEDIA_STORAGE_MODE')
    media_storage_dir: str = Field('media', env='MEDIA_STORAGE_DIR')
    media_store_image: bool = Field(True, env='MEDIA_STORE_IMAGE')
    media_store_audio: bool = Field(True, env='MEDIA_STORE_AUDIO')

    data_retention_days: int = Field(0, env='DATA_RETENTION_DAYS')
    data_retention_max_gb: float = Field(0.0, env='DATA_RETENTION_MAX_GB')
    data_retention_check_interval_seconds: int = Field(3600, env='DATA_RETENTION_CHECK_INTERVAL_SECONDS')
    data_retention_tables: list[str] = Field(default_factory=list, env='DATA_RETENTION_TABLES')

    @validator('cors_allow_origins', pre=True)
    def parse_cors_allow_origins(cls, value: object) -> list[str]:
        """解析 CORS 允许来源配置。"""
        if value is None:
            return []
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
            return [item.strip() for item in stripped.split(',') if item.strip()]
        return value

    @validator('yolo_run_mode', 'audio_run_mode', pre=True)
    def normalize_run_mode(cls, value: object) -> str:
        text = str(value or '').strip().lower()
        if text not in {'thread', 'process'}:
            return 'thread'
        return text

    @validator('media_storage_mode', pre=True)
    def normalize_media_storage_mode(cls, value: object) -> str:
        text = str(value or '').strip().lower()
        if text not in {'database', 'filesystem'}:
            return 'database'
        return text

    @validator('data_retention_tables', pre=True)
    def parse_data_retention_tables(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            return [item.strip() for item in stripped.split(',') if item.strip()]
        return value

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


RUNTIME_HOT_UPDATE_KEYS = {
    'PREFER_PAYLOAD_DEVICE_ID',
    'COMMAND_TIMEOUT_SECONDS',
    'DATA_RETENTION_DAYS',
    'DATA_RETENTION_MAX_GB',
    'DATA_RETENTION_CHECK_INTERVAL_SECONDS',
    'DATA_RETENTION_TABLES',
}
RUNTIME_HOT_UPDATE_PREFIXES = (
    'MQTT_',
    'MYSQL_',
    'YOLO_',
    'AUDIO_',
    'MEDIA_',
    'DATA_RETENTION_',
)


def is_hot_update_key(key: str) -> bool:
    upper = str(key or '').upper()
    if upper in RUNTIME_HOT_UPDATE_KEYS:
        return True
    return upper.startswith(RUNTIME_HOT_UPDATE_PREFIXES)


def merge_runtime_overrides(base: dict, overrides: dict | None) -> dict:
    """合并运行期覆盖配置。"""
    merged = dict(base or {})
    if overrides:
        merged.update(overrides)
    return merged


def split_runtime_override_keys(overrides: dict | None) -> tuple[list[str], list[str]]:
    """区分可热更与需重启的配置键。"""
    keys = list((overrides or {}).keys())
    hot_keys = [key for key in keys if is_hot_update_key(key)]
    restart_keys = [key for key in keys if not is_hot_update_key(key)]
    return hot_keys, restart_keys


@lru_cache()
def get_settings() -> Settings:
    """返回 Settings 单例。"""
    load_env()
    return Settings()
