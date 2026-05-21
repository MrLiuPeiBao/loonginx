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
            'http://localhost:8000',
            'http://127.0.0.1:8000',
            'http://localhost:8080',
            'http://127.0.0.1:8080',
            'http://localhost:3000',
            'http://127.0.0.1:3000',
            'http://localhost:5173',
            'http://127.0.0.1:5173',
        ],
        env='CORS_ALLOW_ORIGINS',
    )

    mqtt_enabled: bool = Field(True, env='MQTT_ENABLED')
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
    sensor_backfill_max_age_seconds: float = Field(12.0, env='SENSOR_BACKFILL_MAX_AGE_SECONDS')
    plc_direct_enabled: bool = Field(False, env='PLC_DIRECT_ENABLED')
    plc_host: str = Field('192.168.2.1', env='PLC_HOST')
    plc_port: int = Field(502, env='PLC_PORT')
    plc_unit_id: int = Field(1, env='PLC_UNIT_ID')
    plc_timeout: float = Field(0.5, env='PLC_TIMEOUT')
    plc_connect_timeout: float = Field(2.0, env='PLC_CONNECT_TIMEOUT')
    plc_even_byte_is_high: bool = Field(True, env='PLC_EVEN_BYTE_IS_HIGH')
    plc_float_word_order: str = Field('big', env='PLC_FLOAT_WORD_ORDER')
    plc_float_byte_order: str = Field('big', env='PLC_FLOAT_BYTE_ORDER')
    plc_status_poll_interval: float = Field(0.5, env='PLC_STATUS_POLL_INTERVAL')
    plc_heartbeat_interval: float = Field(1.0, env='PLC_HEARTBEAT_INTERVAL')
    plc_command_pulse_seconds: float = Field(0.1, env='PLC_COMMAND_PULSE_SECONDS')
    plc_device_id: str = Field('server-plc', env='PLC_DEVICE_ID')
    plc_location: str = Field('server', env='PLC_LOCATION')
    plc_rt_pipe_name: str = Field(r'\\.\pipe\loonginx-plc-rt', env='PLC_RT_PIPE_NAME')
    plc_rt_authkey: str = Field('loonginx-plc-rt', env='PLC_RT_AUTHKEY')
    plc_rt_request_timeout: float = Field(5.0, env='PLC_RT_REQUEST_TIMEOUT')
    plc_rt_connect_retry_seconds: float = Field(2.0, env='PLC_RT_CONNECT_RETRY_SECONDS')
    plc_evt_pipe_name: str = Field(r'\\.\pipe\loonginx-plc-evt', env='PLC_EVT_PIPE_NAME')
    plc_evt_authkey: str = Field('loonginx-plc-evt', env='PLC_EVT_AUTHKEY')
    telemetry_pipe_name: str = Field(r'\\.\pipe\loonginx-telemetry', env='TELEMETRY_PIPE_NAME')
    telemetry_authkey: str = Field('loonginx-telemetry', env='TELEMETRY_AUTHKEY')
    plc_timing_trace_enabled: bool = Field(False, env='PLC_TIMING_TRACE_ENABLED')
    plc_timing_trace_log: str = Field('logs/plc_timing.log', env='PLC_TIMING_TRACE_LOG')
    plc_timing_trace_include_poll: bool = Field(True, env='PLC_TIMING_TRACE_INCLUDE_POLL')
    plc_timing_trace_include_headers: bool = Field(True, env='PLC_TIMING_TRACE_INCLUDE_HEADERS')

    yolo_enabled: bool = Field(False, env='YOLO_ENABLED')
    yolo_run_mode: str = Field('thread', env='YOLO_RUN_MODE')
    yolo_rtsp_input: str = Field('', env='YOLO_RTSP_INPUT')
    yolo_rtsp_output: str = Field('rtsp://127.0.0.1:8555/yolo', env='YOLO_RTSP_OUTPUT')
    yolo_rtsp_backend: str = Field('gstreamer', env='YOLO_RTSP_BACKEND')
    yolo_rtsp_capture_options: str = Field('', env='YOLO_RTSP_CAPTURE_OPTIONS')
    yolo_capture_buffer_size: int = Field(1, env='YOLO_CAPTURE_BUFFER_SIZE')
    yolo_drop_frames: bool = Field(True, env='YOLO_DROP_FRAMES')
    yolo_drop_max_frames: int = Field(10, env='YOLO_DROP_MAX_FRAMES')
    yolo_read_failure_reconnect_threshold: int = Field(5, env='YOLO_READ_FAILURE_RECONNECT_THRESHOLD')
    yolo_rtsp_low_latency: bool = Field(True, env='YOLO_RTSP_LOW_LATENCY')
    yolo_gst_encoder: str = Field('x264enc', env='YOLO_GST_ENCODER')
    yolo_gst_encoder_props: str = Field('', env='YOLO_GST_ENCODER_PROPS')
    yolo_model_path: str = Field('yolov8n.pt', env='YOLO_MODEL_PATH')
    yolo_compute_device: str = Field('auto', env='YOLO_COMPUTE_DEVICE')
    yolo_device_id: str = Field('camera', env='YOLO_DEVICE_ID')
    yolo_location: str = Field('rtsp', env='YOLO_LOCATION')
    yolo_screenshot_interval: float = Field(5.0, env='YOLO_SCREENSHOT_INTERVAL')
    yolo_confidence_threshold: float = Field(0.35, env='YOLO_CONFIDENCE_THRESHOLD')
    yolo_frame_width: int = Field(640, env='YOLO_FRAME_WIDTH')
    yolo_frame_height: int = Field(360, env='YOLO_FRAME_HEIGHT')
    yolo_fps: int = Field(10, env='YOLO_FPS')
    yolo_ffmpeg_enabled: bool = Field(True, env='YOLO_FFMPEG_ENABLED')
    yolo_tracker_config: str = Field('bytetrack.yaml', env='YOLO_TRACKER_CONFIG')
    yolo_person_confidence: Optional[float] = Field(None, env='YOLO_PERSON_CONFIDENCE')
    yolo_detection_interval: float = Field(5.0, env='YOLO_DETECTION_INTERVAL')
    yolo_inference_interval: float = Field(0.5, env='YOLO_INFERENCE_INTERVAL')
    yolo_inference_size: int = Field(512, env='YOLO_INFERENCE_SIZE')
    yolo_rtsp_failure_backoff_initial_seconds: float = Field(
        10.0,
        env='YOLO_RTSP_FAILURE_BACKOFF_INITIAL_SECONDS',
    )
    yolo_rtsp_failure_backoff_max_seconds: float = Field(
        120.0,
        env='YOLO_RTSP_FAILURE_BACKOFF_MAX_SECONDS',
    )
    yolo_inference_timeout_seconds: float = Field(8.0, env='YOLO_INFERENCE_TIMEOUT_SECONDS')
    yolo_inference_timeout_consecutive_limit: int = Field(
        1,
        env='YOLO_INFERENCE_TIMEOUT_CONSECUTIVE_LIMIT',
    )
    yolo_watchdog_reconnect_cooldown_seconds: float = Field(
        2.0,
        env='YOLO_WATCHDOG_RECONNECT_COOLDOWN_SECONDS',
    )
    yolo_watchdog_max_stream_restarts: int = Field(
        3,
        env='YOLO_WATCHDOG_MAX_STREAM_RESTARTS',
    )

    audio_enabled: bool = Field(False, env='AUDIO_ENABLED')
    audio_run_mode: str = Field('thread', env='AUDIO_RUN_MODE')
    audio_rtsp_input: str = Field('', env='AUDIO_RTSP_INPUT')
    audio_device_id: str = Field('mic', env='AUDIO_DEVICE_ID')
    audio_location: str = Field('rtsp', env='AUDIO_LOCATION')
    audio_sample_rate: int = Field(16000, env='AUDIO_SAMPLE_RATE')
    audio_window_seconds: float = Field(1.0, env='AUDIO_WINDOW_SECONDS')
    audio_store_min_interval_seconds: float = Field(30.0, env='AUDIO_STORE_MIN_INTERVAL_SECONDS')
    audio_threshold_centroid: float = Field(0.0, env='AUDIO_THRESHOLD_CENTROID')
    audio_threshold_bandwidth: float = Field(0.0, env='AUDIO_THRESHOLD_BANDWIDTH')
    audio_threshold_rolloff: float = Field(0.0, env='AUDIO_THRESHOLD_ROLLOFF')
    audio_threshold_flatness: float = Field(0.0, env='AUDIO_THRESHOLD_FLATNESS')
    audio_threshold_flux: float = Field(0.0, env='AUDIO_THRESHOLD_FLUX')
    audio_threshold_rms: float = Field(0.0, env='AUDIO_THRESHOLD_RMS')
    audio_debug_store_all: bool = Field(False, env='AUDIO_DEBUG_STORE_ALL')
    audio_rtsp_failure_backoff_initial_seconds: float = Field(
        10.0,
        env='AUDIO_RTSP_FAILURE_BACKOFF_INITIAL_SECONDS',
    )
    audio_rtsp_failure_backoff_max_seconds: float = Field(
        120.0,
        env='AUDIO_RTSP_FAILURE_BACKOFF_MAX_SECONDS',
    )

    media_gateway_enabled: bool = Field(False, env='MEDIA_GATEWAY_ENABLED')
    media_gateway_type: str = Field('custom', env='MEDIA_GATEWAY_TYPE')
    media_gateway_run_mode: str = Field('process', env='MEDIA_GATEWAY_RUN_MODE')
    media_gateway_exec: str = Field('', env='MEDIA_GATEWAY_EXEC')
    media_gateway_args: str = Field('', env='MEDIA_GATEWAY_ARGS')
    media_gateway_workdir: str = Field('', env='MEDIA_GATEWAY_WORKDIR')
    media_gateway_relay_rtsp: str = Field('', env='MEDIA_GATEWAY_RELAY_RTSP')
    media_gateway_control_panel_rtsp: str = Field('', env='MEDIA_GATEWAY_CONTROL_PANEL_RTSP')
    media_gateway_http_api: str = Field('http://127.0.0.1:1984', env='MEDIA_GATEWAY_HTTP_API')
    media_gateway_rewrite_yolo_input: bool = Field(True, env='MEDIA_GATEWAY_REWRITE_YOLO_INPUT')
    media_gateway_rewrite_audio_input: bool = Field(True, env='MEDIA_GATEWAY_REWRITE_AUDIO_INPUT')

    media_storage_mode: str = Field('filesystem', env='MEDIA_STORAGE_MODE')
    media_storage_dir: str = Field('media', env='MEDIA_STORAGE_DIR')
    media_store_image: bool = Field(True, env='MEDIA_STORE_IMAGE')
    media_store_audio: bool = Field(True, env='MEDIA_STORE_AUDIO')
    media_write_queue_capacity: int = Field(1000, env='MEDIA_WRITE_QUEUE_CAPACITY')
    media_read_query_timeout_seconds: float = Field(2.0, env='MEDIA_READ_QUERY_TIMEOUT_SECONDS')

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

    @validator('yolo_run_mode', 'audio_run_mode', 'media_gateway_run_mode', pre=True)
    def normalize_run_mode(cls, value: object) -> str:
        text = str(value or '').strip().lower()
        if text not in {'thread', 'process'}:
            return 'thread'
        return text

    @validator('media_gateway_type', pre=True)
    def normalize_media_gateway_type(cls, value: object) -> str:
        text = str(value or 'custom').strip().lower()
        if text not in {'custom', 'mediamtx', 'go2rtc', 'none'}:
            return 'custom'
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

        @classmethod
        def parse_env_var(cls, field_name: str, raw_val: str) -> object:
            if field_name in {'cors_allow_origins', 'data_retention_tables'}:
                return raw_val
            return json.loads(raw_val)

    @property
    def effective_cors_allow_origins(self) -> list[str]:
        """CORS origins with local API access origins always included."""
        origins = list(self.cors_allow_origins)
        origins.extend([
            f'http://localhost:{self.api_port}',
            f'http://127.0.0.1:{self.api_port}',
        ])

        api_host = (self.api_host or '').strip().strip('[]')
        if api_host and api_host not in {'0.0.0.0', '::', '+', '*'}:
            origin_host = f'[{api_host}]' if ':' in api_host else api_host
            origins.append(f'http://{origin_host}:{self.api_port}')

        seen: set[str] = set()
        unique_origins: list[str] = []
        for origin in origins:
            normalized = str(origin).strip().rstrip('/')
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            unique_origins.append(normalized)
        return unique_origins

    def is_media_gateway_enabled(self) -> bool:
        """Whether media gateway is enabled for unified ingress."""
        gateway_type = str(self.media_gateway_type or '').strip().lower()
        return bool(self.media_gateway_enabled and gateway_type != 'none')

    def resolve_yolo_rtsp_input(self) -> str:
        """Resolve effective YOLO RTSP source (gateway relay preferred)."""
        direct_input = str(self.yolo_rtsp_input or '').strip()
        relay_input = str(self.media_gateway_relay_rtsp or '').strip()
        if self.is_media_gateway_enabled() and self.media_gateway_rewrite_yolo_input and relay_input:
            return relay_input
        return direct_input

    def resolve_audio_rtsp_input(self) -> str:
        """Resolve effective audio RTSP source (gateway relay preferred)."""
        direct_input = str(self.audio_rtsp_input or '').strip()
        relay_input = str(self.media_gateway_relay_rtsp or '').strip()
        if self.is_media_gateway_enabled() and self.media_gateway_rewrite_audio_input and relay_input:
            return relay_input
        return direct_input

    def resolve_control_panel_rtsp_input(self) -> str:
        """Resolve RTSP source for control-panel to keep a single ingress point."""
        if not self.is_media_gateway_enabled():
            return ''
        preferred = (
            str(self.media_gateway_control_panel_rtsp or '').strip()
            or str(self.media_gateway_relay_rtsp or '').strip()
        )
        return preferred

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
    'PLC_',
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
