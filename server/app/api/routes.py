"""REST API routes."""

from __future__ import annotations

import base64
import json
import logging
import os
import sys
import threading
import time
import traceback
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Union, TYPE_CHECKING, get_args, get_origin

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from sqlmodel import Session

from app.core.config import (
    Settings,
    get_settings,
    is_hot_update_key,
    merge_runtime_overrides,
    split_runtime_override_keys,
)
from app.core.constants import MQTT_TOPICS
from app.db.models import (
    AlarmRecord,
    AudioData,
    BMSData,
    CablewayStatus,
    CommandDirection,
    CommandLog,
    CommandRequestState,
    CommandStatus,
    ImageData,
    MetalAnomaly,
    RFIDData,
    SensorConfig as SensorConfigModel,
    SensorData,
)
from app.db.session import db_ping, get_session, rebuild_engine
from app.schemas.alarm import AlarmRecordCreate, AlarmRecordRead
from app.schemas.audio import AudioDataCreate, AudioDataRead
from app.db.audio_thresholds import AudioThreshold
from app.schemas.bms import BMSDataCreate, BMSDataRead
from app.schemas.cableway import CablewayCommandRequest, CablewayStatusRead
from app.schemas.command import CommandLogRead, CommandRequest, CommandRequestStatusRead
from app.schemas.config import SensorConfigCreate, SensorConfigRead
from app.schemas.image import ImageDataCreate, ImageDataRead
from app.schemas.metal import MetalAnomalyCreate, MetalAnomalyRead
from app.schemas.rfid import RFIDDataCreate, RFIDDataRead
from app.schemas.sensor import SensorDataCreate, SensorDataRead
from app.services.data_service import DataService
from app.services.alarm_publisher import build_alarm_event, publish_alarm_event
from app.services.alarm_cache import get_latest_alarm
from app.services.bms_alerts import maybe_create_bms_low_voltage_alarm
from app.services.bms_cache import get_latest_bms, set_latest_bms
from app.services.cableway_cache import get_latest_cableway_status as get_cached_cableway_status
from app.services.cableway_specs import validate_control_command_code, validate_param_updates
from app.services.media_storage import load_file_base64
from app.services.plc_logging import get_plc_logger
from app.services.rfid_cache import get_latest_rfid, set_latest_rfid
from app.services.sensor_cache import get_latest_sensor, set_latest_sensor
from app.services.runtime_config import load_runtime_overrides, save_runtime_overrides
from app.services.env_store import load_env_entries, serialize_env_value, update_env_file

if TYPE_CHECKING:
    from app.services.audio_service import AudioMonitorService
else:
    from app.services.audio_service import AudioMonitorService


router = APIRouter(prefix='/api', tags=['api'])
logger = logging.getLogger(__name__)
plc_logger = get_plc_logger(__name__)
DEFAULT_QUERY_LIMIT = 200
DEFAULT_QUERY_LOOKBACK_HOURS = 24
_ENV_PATH = Path(__file__).resolve().parents[2] / '.env'
_BOOL_TRUE_VALUES = {'true', '1', 'yes', 'on'}
_BOOL_FALSE_VALUES = {'false', '0', 'no', 'off'}


def get_data_service(session: Session = Depends(get_session)) -> DataService:
    """Return a DataService instance."""
    return DataService(session)

def get_audio_service(request: Request) -> "AudioMonitorService":
    """Return audio monitor service from app state if available."""
    service = getattr(request.app.state, 'audio', None)
    if service is None:
        raise HTTPException(status_code=503, detail='Audio service not available')
    return service


def _settings_to_env_dict(settings: Settings) -> Dict[str, object]:
    data: Dict[str, object] = {}
    fields = getattr(settings, '__fields__', {})
    for name, field in fields.items():
        env = None
        if hasattr(field, 'field_info'):
            extra = getattr(field.field_info, 'extra', {}) or {}
            env = extra.get('env') or extra.get('env_names')
            if env is None:
                env = getattr(field.field_info, 'env', None)
        env_name = None
        if isinstance(env, (list, set, tuple)):
            env_name = next(iter(env), None)
        elif isinstance(env, str):
            env_name = env
        key = env_name or name.upper()
        data[key] = getattr(settings, name)
    return data


def _get_field_env_name(name: str, field: Any) -> str:
    env = None
    if hasattr(field, 'field_info'):
        extra = getattr(field.field_info, 'extra', {}) or {}
        env = extra.get('env') or extra.get('env_names')
        if env is None:
            env = getattr(field.field_info, 'env', None)
    if env is None:
        env = getattr(field, 'validation_alias', None)
    env_name = None
    if isinstance(env, (list, set, tuple)):
        env_name = next(iter(env), None)
    elif isinstance(env, str):
        env_name = env
    return env_name or name.upper()


def _resolve_optional_type(value: Any) -> Any:
    origin = get_origin(value)
    if origin is Union:
        args = [arg for arg in get_args(value) if arg is not type(None)]
        if args:
            return _resolve_optional_type(args[0])
    return value


def _infer_option_type(value: Any) -> str:
    resolved = _resolve_optional_type(value)
    origin = get_origin(resolved)
    if origin in (list, List):
        return 'list'
    if resolved is bool:
        return 'bool'
    if resolved is int:
        return 'int'
    if resolved is float:
        return 'float'
    return 'text'


def _infer_env_option_type(value: str) -> str:
    text = str(value or '').strip().lower()
    if text in _BOOL_TRUE_VALUES or text in _BOOL_FALSE_VALUES:
        return 'bool'
    return 'text'


def _coerce_env_default(value: str, opt_type: str) -> object:
    if opt_type == 'bool':
        return str(value or '').strip().lower() in _BOOL_TRUE_VALUES
    return value


def _collect_option_meta(
    settings: Settings,
    env_entries: List[Dict[str, str]],
) -> Dict[str, Dict[str, Any]]:
    meta: Dict[str, Dict[str, Any]] = {}
    fields = getattr(settings, '__fields__', None) or getattr(settings, 'model_fields', {})
    for name, field in fields.items():
        key = _get_field_env_name(name, field)
        py_type = (
            getattr(field, 'outer_type_', None)
            or getattr(field, 'annotation', None)
            or getattr(field, 'type_', None)
        )
        meta[key] = {
            'type': _infer_option_type(py_type),
            'default': getattr(settings, name),
            'source': 'settings',
        }
    for entry in env_entries:
        key = entry.get('key')
        if not key or key in meta:
            continue
        opt_type = _infer_env_option_type(entry.get('value', ''))
        meta[key] = {
            'type': opt_type,
            'default': _coerce_env_default(entry.get('value', ''), opt_type),
            'source': 'env',
        }
    return meta


def _build_base_env_config(settings: Settings, env_entries: List[Dict[str, str]]) -> Dict[str, object]:
    base = _settings_to_env_dict(settings)
    for entry in env_entries:
        key = entry.get('key')
        if not key or key in base:
            continue
        opt_type = _infer_env_option_type(entry.get('value', ''))
        if opt_type == 'bool':
            base[key] = _coerce_env_default(entry.get('value', ''), opt_type)
        else:
            base[key] = entry.get('value', '')
    return base


def _parse_bool_value(value: object, *, key: Optional[str] = None) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if value == 1:
            return True
        if value == 0:
            return False
    text = '' if value is None else str(value).strip().lower()
    if text in _BOOL_TRUE_VALUES:
        return True
    if text in _BOOL_FALSE_VALUES:
        return False
    label = key or '值'
    raise HTTPException(status_code=400, detail=f'{label} 需要布尔值')


def _parse_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    return [item.strip() for item in text.split(',') if item.strip()]


def _normalize_payload_value(key: str, value: object, meta: Dict[str, Any]) -> object:
    opt_type = str(meta.get('type') or 'text')
    source = meta.get('source')
    if opt_type == 'bool':
        parsed = _parse_bool_value(value, key=key)
        if source == 'env':
            return serialize_env_value(parsed)
        return parsed
    if opt_type == 'int':
        text = '' if value is None else str(value).strip()
        if text == '':
            raise HTTPException(status_code=400, detail=f'{key} 需要整数值')
        try:
            return int(text)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f'{key} 需要整数值') from exc
    if opt_type == 'float':
        text = '' if value is None else str(value).strip()
        if text == '':
            raise HTTPException(status_code=400, detail=f'{key} 需要数值')
        try:
            return float(text)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f'{key} 需要数值') from exc
    if opt_type == 'list':
        return _parse_list(value)
    text = '' if value is None else str(value)
    if source == 'env':
        return serialize_env_value(text)
    return text


def _normalize_current_value(value: object, meta: Dict[str, Any]) -> object:
    opt_type = str(meta.get('type') or 'text')
    if opt_type == 'bool':
        text = str(value or '').strip().lower()
        return text in _BOOL_TRUE_VALUES
    if opt_type == 'int':
        try:
            return int(value)  # type: ignore[arg-type]
        except Exception:
            return value
    if opt_type == 'float':
        try:
            return float(value)  # type: ignore[arg-type]
        except Exception:
            return value
    if opt_type == 'list':
        return _parse_list(value)
    return '' if value is None else str(value)


_SECTION_DEFS: Dict[str, Dict[str, str]] = {
    'general': {'title': '通用', 'description': ''},
    'api': {'title': 'API / CORS', 'description': ''},
    'mqtt': {'title': 'MQTT', 'description': ''},
    'mysql': {'title': 'MySQL', 'description': ''},
    'yolo': {'title': 'YOLO', 'description': ''},
    'audio': {'title': '音频', 'description': ''},
    'media': {'title': '媒体存储', 'description': ''},
    'data_retention': {'title': '数据留存', 'description': ''},
    'command': {'title': '指令/超时', 'description': ''},
    'startup': {'title': '启动/脚本', 'description': ''},
    'other': {'title': '其他', 'description': ''},
}
_SECTION_ORDER = [
    'general',
    'api',
    'mqtt',
    'mysql',
    'yolo',
    'audio',
    'media',
    'data_retention',
    'command',
    'startup',
    'other',
]
_OPTION_CHOICES: Dict[str, List[str]] = {
    'YOLO_RUN_MODE': ['thread', 'process'],
    'AUDIO_RUN_MODE': ['thread', 'process'],
    'MEDIA_STORAGE_MODE': ['database', 'filesystem'],
    'YOLO_RTSP_BACKEND': ['gstreamer', 'ffmpeg'],
}


def _resolve_section_key(key: str) -> str:
    upper = str(key or '').upper()
    if upper.startswith('MQTT_'):
        return 'mqtt'
    if upper.startswith('MYSQL_'):
        return 'mysql'
    if upper.startswith('YOLO_'):
        return 'yolo'
    if upper.startswith('AUDIO_'):
        return 'audio'
    if upper.startswith('MEDIA_'):
        return 'media'
    if upper.startswith('DATA_RETENTION_'):
        return 'data_retention'
    if upper.startswith('COMMAND_'):
        return 'command'
    if upper.startswith('API_') or upper.startswith('CORS_') or upper.startswith('UVICORN_'):
        return 'api'
    if upper in {'APP_NAME', 'PREFER_PAYLOAD_DEVICE_ID'}:
        return 'general'
    if upper.startswith('GUI_') or upper in {'SKIP_PIP_INSTALL', 'INSTALL_DEPENDENCIES'}:
        return 'startup'
    return 'other'


def _build_runtime_config_sections(
    settings: Settings,
    env_entries: List[Dict[str, str]],
) -> List[Dict[str, object]]:
    option_meta = _collect_option_meta(settings, env_entries)
    comments = {entry.get('key'): entry.get('comment', '') for entry in env_entries if entry.get('key')}
    env_order = [entry.get('key') for entry in env_entries if entry.get('key')]

    sections: Dict[str, Dict[str, object]] = {}
    for key in _SECTION_ORDER:
        section_def = _SECTION_DEFS.get(key, {})
        sections[key] = {
            'key': key,
            'title': section_def.get('title', key),
            'description': section_def.get('description', ''),
            'options': [],
        }

    def add_option(option_key: str) -> None:
        meta = option_meta.get(option_key, {})
        opt_type = str(meta.get('type') or 'text')
        option: Dict[str, object] = {
            'key': option_key,
            'label': option_key,
            'type': opt_type,
            'default': meta.get('default', ''),
            'hint': comments.get(option_key, ''),
            'effect': '热更新' if is_hot_update_key(option_key) else '自动重启',
        }
        choices = _OPTION_CHOICES.get(option_key)
        if choices:
            option['type'] = 'select'
            option['choices'] = choices
        section_key = _resolve_section_key(option_key)
        sections[section_key]['options'].append(option)

    for key in env_order:
        if key in option_meta:
            add_option(key)

    remaining = sorted(set(option_meta.keys()) - set(env_order))
    for key in remaining:
        add_option(key)

    return [sections[key] for key in _SECTION_ORDER if sections[key]['options']]


def _schedule_restart(settings: Settings, delay_seconds: float = 1.0) -> None:
    def _restart() -> None:
        time.sleep(max(0.2, float(delay_seconds)))
        args = [
            sys.executable,
            '-m',
            'uvicorn',
            'main:app',
            '--host',
            str(settings.api_host),
            '--port',
            str(settings.api_port),
        ]
        os.execv(sys.executable, args)

    threading.Thread(target=_restart, name='api-restart', daemon=True).start()


def _write_runtime_config_debug(info: Dict[str, Any]) -> None:
    log_path = Path(__file__).resolve().parents[2] / 'logs' / 'runtime_config_debug.log'
    log_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {'timestamp': datetime.now().isoformat(timespec='seconds')}
    payload.update(info or {})
    with log_path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, default=str))
        handle.write('\n')


def _is_probable_wav(data: bytes) -> bool:
    """Heuristic check for WAV container header."""
    return len(data) >= 12 and data[:4] in (b'RIFF', b'RIFX') and data[8:12] == b'WAVE'


def _audio_db_value_to_base64(value: object, audio_path: Optional[str] = None) -> str:
    """Convert DB audio payload (bytes or legacy text) into base64 string for JSON."""
    if audio_path:
        from_file = load_file_base64(audio_path)
        if from_file is not None:
            return from_file
    if value is None:
        return ''
    if isinstance(value, str):
        # Legacy rows stored as base64 LONGTEXT
        return value
    if not isinstance(value, (bytes, bytearray)):
        return str(value)

    payload = bytes(value)
    if _is_probable_wav(payload):
        return base64.b64encode(payload).decode('ascii')

    # Rows might have been migrated from LONGTEXT -> BLOB, keeping base64 ASCII bytes.
    try:
        text = payload.decode('ascii')
    except UnicodeDecodeError:
        return base64.b64encode(payload).decode('ascii')

    compact = ''.join(text.split())
    try:
        decoded = base64.b64decode(compact, validate=False)
        if _is_probable_wav(decoded):
            return compact
    except Exception:
        pass

    return base64.b64encode(payload).decode('ascii')


def _image_db_value_to_base64(value: object, image_path: Optional[str] = None) -> str:
    """Convert DB image payload (base64 or file path) into base64 string."""
    if image_path:
        from_file = load_file_base64(image_path)
        if from_file is not None:
            return from_file
    if value is None:
        return ''
    if isinstance(value, str):
        return value
    if isinstance(value, (bytes, bytearray)):
        return base64.b64encode(bytes(value)).decode('ascii')
    return str(value)


def _decode_audio_base64(value: str) -> bytes:
    """Decode base64 audio payload sent via API."""
    text = (value or '').strip()
    if not text:
        raise HTTPException(status_code=400, detail='audio_data is required')
    try:
        return base64.b64decode(text, validate=False)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f'Invalid audio_data base64: {exc}') from exc


def _parse_datetime(value: Optional[str], field: str) -> Optional[datetime]:
    """解析查询参数中的 datetime 字符串。"""
    if value is None or value == '':
        return None
    text = str(value).strip()
    try:
        ts = float(text)
        if ts > 1_000_000_000_000:
            ts = ts / 1000.0
        return datetime.fromtimestamp(ts)
    except (ValueError, TypeError):
        pass
    try:
        return datetime.fromisoformat(text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f'Invalid {field}: {value}') from exc


def _parse_bool(value: Optional[str], field: str) -> Optional[bool]:
    """解析布尔查询参数。"""
    if value is None:
        return None
    lowered = value.strip().lower()
    if lowered in {'1', 'true', 'yes'}:
        return True
    if lowered in {'0', 'false', 'no'}:
        return False
    raise HTTPException(status_code=400, detail=f'Invalid {field}: {value}')


def _parse_direction(value: Optional[str]) -> Optional[CommandDirection]:
    """解析命令方向参数。"""
    if value is None:
        return None
    try:
        return CommandDirection(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f'Invalid command direction: {value}',
        ) from exc


def _parse_command_status(value: Optional[str]) -> Optional[CommandStatus]:
    """解析命令状态参数。"""
    if value is None:
        return None
    try:
        return CommandStatus(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f'Invalid command status: {value}',
        ) from exc


def _apply_default_query_window(
    start_at: Optional[datetime],
    end_at: Optional[datetime],
    limit: Optional[int],
) -> tuple[Optional[datetime], Optional[datetime], int]:
    if start_at is None and end_at is None:
        start_at = datetime.now() - timedelta(hours=DEFAULT_QUERY_LOOKBACK_HOURS)
    return start_at, end_at, limit or DEFAULT_QUERY_LIMIT


def _normalize_command_payload(raw: Union[str, List[int]]) -> str:
    """将命令载荷统一为十六进制字符串。"""
    if isinstance(raw, list):
        if not raw:
            raise HTTPException(status_code=400, detail='Empty payload list')
        return ' '.join(f'{int(item) & 0xFF:02X}' for item in raw)

    if isinstance(raw, str):
        cleaned = raw.replace(',', ' ')
        segments = [segment for segment in cleaned.split() if segment]
        if not segments:
            raise HTTPException(status_code=400, detail='Empty payload string')
        hex_bytes: List[str] = []
        for segment in segments:
            try:
                hex_bytes.append(f'{int(segment, 16) & 0xFF:02X}')
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f'Invalid hex segment: {segment}',
                ) from exc
        return ' '.join(hex_bytes)

    raise HTTPException(status_code=400, detail='Unsupported payload format')


def _hex_to_bytes(hex_string: str) -> bytes:
    """将空格分隔的十六进制字符串转换为字节序列。"""
    segments = [segment for segment in hex_string.split() if segment]
    return bytes(int(segment, 16) & 0xFF for segment in segments)


@router.get('/health')
def health(request: Request, settings: Settings = Depends(get_settings)) -> dict:
    """健康检查。"""
    mqtt_manager = getattr(request.app.state, 'mqtt', None)
    mqtt_ok = bool(getattr(mqtt_manager, 'is_connected', False))
    status_payload = {
        'db_ok': bool(db_ping()),
        'mqtt_ok': mqtt_ok,
    }
    supervisor = getattr(request.app.state, 'supervisor', None)
    if supervisor and hasattr(supervisor, 'get_status'):
        try:
            status_payload.update(supervisor.get_status())
        except Exception:
            logger.debug('Failed to read supervisor status', exc_info=True)
    return {
        'status': 'ok',
        'app': settings.app_name,
        'timestamp': datetime.now().isoformat(timespec='seconds'),
        **status_payload,
    }


@router.get('/health/live')
def health_live(settings: Settings = Depends(get_settings)) -> dict:
    """Liveness probe: process is running."""
    return {
        'status': 'ok',
        'app': settings.app_name,
        'timestamp': datetime.now().isoformat(timespec='seconds'),
    }


@router.get('/health/ready')
def health_ready(request: Request, settings: Settings = Depends(get_settings)) -> JSONResponse:
    """Readiness probe: DB + MQTT must be available."""
    mqtt_manager = getattr(request.app.state, 'mqtt', None)
    mqtt_ok = bool(getattr(mqtt_manager, 'is_connected', False))
    payload: Dict[str, object] = {
        'status': 'ok',
        'app': settings.app_name,
        'timestamp': datetime.now().isoformat(timespec='seconds'),
        'db_ok': bool(db_ping()),
        'mqtt_ok': mqtt_ok,
    }
    supervisor = getattr(request.app.state, 'supervisor', None)
    if supervisor and hasattr(supervisor, 'get_status'):
        try:
            payload.update(supervisor.get_status())
        except Exception:
            logger.debug('Failed to read supervisor status', exc_info=True)

    if payload.get('db_ok') and payload.get('mqtt_ok'):
        return JSONResponse(status_code=200, content=payload)

    payload['status'] = 'not_ready'
    return JSONResponse(status_code=503, content=payload)


@router.get('/sensors', response_model=List[SensorDataRead])
def list_sensor_data(
    start: Optional[str] = Query(default=None, description='起始时间，ISO8601'),
    end: Optional[str] = Query(default=None, description='结束时间，ISO8601'),
    device_id: Optional[str] = Query(default=None, description='设备编号'),
    location: Optional[str] = Query(default=None, description='安装位置'),
    limit: Optional[int] = Query(default=None, ge=1, le=1000, description='返回数量上限'),
    offset: int = Query(default=0, ge=0, description='分页偏移量'),
    data_service: DataService = Depends(get_data_service),
) -> List[SensorData]:
    """查询传感器数据。"""
    if limit == 1 and offset == 0 and start is None and end is None:
        cached = get_latest_sensor()
        if cached:
            cached_device = cached.get('device_id')
            cached_location = cached.get('location')
            if (device_id is None or device_id == cached_device) and (
                location is None or location == cached_location
            ):
                return [SensorDataRead(**cached)]
    start_at = _parse_datetime(start, 'start')
    end_at = _parse_datetime(end, 'end')
    start_at, end_at, limit = _apply_default_query_window(start_at, end_at, limit)
    return data_service.list_sensor_data(
        start=start_at,
        end=end_at,
        device_id=device_id,
        location=location,
        limit=limit,
        offset=offset,
    )


@router.post('/sensors', response_model=List[SensorDataRead], status_code=status.HTTP_201_CREATED)
def create_sensor_data(
    payload: Union[SensorDataCreate, List[SensorDataCreate]],
    http_request: Request,
    data_service: DataService = Depends(get_data_service),
) -> List[SensorData]:
    """插入传感器数据。"""
    items = payload if isinstance(payload, list) else [payload]
    entities = [SensorData(**item.dict()) for item in items]
    latest_rfid = data_service.get_latest_rfid_card()
    if latest_rfid:
        for entity in entities:
            entity.location = latest_rfid
    stored = data_service.create_sensor_data(entities)
    if stored:
        latest = max(stored, key=lambda item: item.timestamp)
        set_latest_sensor(latest)
    mqtt_manager = getattr(http_request.app.state, 'mqtt', None)
    for event in data_service.consume_alarm_events():
        publish_alarm_event(mqtt_manager, event)
    return stored


@router.get('/sensors/latest', response_model=List[SensorDataRead])
def latest_sensor_data(
    limit: int = Query(default=10, ge=1, le=100, description='返回最新的记录数量'),
    data_service: DataService = Depends(get_data_service),
) -> List[SensorData]:
    """查询最新的传感器数据。"""
    if limit == 1:
        cached = get_latest_sensor()
        if cached:
            return [SensorDataRead(**cached)]
    return data_service.list_sensor_data(limit=limit)


@router.get('/bms', response_model=List[BMSDataRead])
def list_bms_data(
    start: Optional[str] = Query(default=None, description='起始时间，ISO8601'),
    end: Optional[str] = Query(default=None, description='结束时间，ISO8601'),
    device_id: Optional[str] = Query(default=None, description='设备编号'),
    limit: Optional[int] = Query(default=None, ge=1, le=1000, description='返回数量上限'),
    offset: int = Query(default=0, ge=0, description='分页偏移量'),
    data_service: DataService = Depends(get_data_service),
) -> List[BMSData]:
    """查询 BMS 数据。"""
    if limit == 1 and offset == 0 and start is None and end is None:
        cached = get_latest_bms()
        if cached and (device_id is None or device_id == cached.get('device_id')):
            return [BMSDataRead(**cached)]
    start_at = _parse_datetime(start, 'start')
    end_at = _parse_datetime(end, 'end')
    start_at, end_at, limit = _apply_default_query_window(start_at, end_at, limit)
    return data_service.list_bms_data(
        start=start_at,
        end=end_at,
        device_id=device_id,
        limit=limit,
        offset=offset,
    )


@router.post('/bms', response_model=List[BMSDataRead], status_code=status.HTTP_201_CREATED)
def create_bms_data(
    payload: Union[BMSDataCreate, List[BMSDataCreate]],
    http_request: Request,
    data_service: DataService = Depends(get_data_service),
) -> List[BMSData]:
    """插入 BMS 数据。"""
    items = payload if isinstance(payload, list) else [payload]
    entities = [BMSData(**item.dict()) for item in items]
    latest_rfid = data_service.get_latest_rfid_card()
    if latest_rfid:
        for entity in entities:
            entity.location = latest_rfid
    stored = data_service.create_bms_data(entities)
    if stored:
        latest = max(stored, key=lambda item: item.timestamp)
        set_latest_bms(latest)
    mqtt_manager = getattr(http_request.app.state, 'mqtt', None)
    for entity in stored:
        alarm_event = maybe_create_bms_low_voltage_alarm(data_service, entity)
        if alarm_event:
            publish_alarm_event(mqtt_manager, alarm_event)
    return stored


@router.get('/rfid', response_model=List[RFIDDataRead])
def list_rfid_data(
    start: Optional[str] = Query(default=None, description='起始时间，ISO8601'),
    end: Optional[str] = Query(default=None, description='结束时间，ISO8601'),
    device_id: Optional[str] = Query(default=None, description='设备编号'),
    limit: Optional[int] = Query(default=None, ge=1, le=1000, description='返回数量上限'),
    offset: int = Query(default=0, ge=0, description='分页偏移量'),
    data_service: DataService = Depends(get_data_service),
) -> List[RFIDData]:
    """查询 RFID 数据。"""
    if limit == 1 and offset == 0 and start is None and end is None:
        cached = get_latest_rfid()
        if cached and (device_id is None or device_id == cached.device_id):
            return [RFIDDataRead(**asdict(cached))]
    start_at = _parse_datetime(start, 'start')
    end_at = _parse_datetime(end, 'end')
    start_at, end_at, limit = _apply_default_query_window(start_at, end_at, limit)
    return data_service.list_rfid_data(
        start=start_at,
        end=end_at,
        device_id=device_id,
        limit=limit,
        offset=offset,
    )


@router.get('/rfid/latest', response_model=RFIDDataRead)
def get_latest_rfid_data(
    device_id: Optional[str] = Query(default=None, description='设备编号'),
    data_service: DataService = Depends(get_data_service),
) -> RFIDData:
    """获取最新 RFID 数据。"""
    snapshot = get_latest_rfid()
    if snapshot and (device_id is None or snapshot.device_id == device_id):
        return RFIDDataRead(**asdict(snapshot))

    items = data_service.list_rfid_data(device_id=device_id, limit=1)
    if not items:
        raise HTTPException(status_code=404, detail='RFID data not found')
    return items[0]


@router.post('/rfid', response_model=List[RFIDDataRead], status_code=status.HTTP_201_CREATED)
def create_rfid_data(
    payload: Union[RFIDDataCreate, List[RFIDDataCreate]],
    data_service: DataService = Depends(get_data_service),
) -> List[RFIDData]:
    """插入 RFID 数据。"""
    items = payload if isinstance(payload, list) else [payload]
    entities = [RFIDData(**item.dict()) for item in items]
    stored = data_service.create_rfid_data(entities)
    if stored:
        latest = max(stored, key=lambda item: item.timestamp)
        set_latest_rfid(latest)
    return stored


@router.get('/commands', response_model=List[CommandLogRead])
def list_command_logs(
    direction: Optional[str] = Query(default=None, description='命令方向 request/response'),
    limit: Optional[int] = Query(default=50, ge=1, le=1000, description='返回数量上限'),
    offset: int = Query(default=0, ge=0, description='分页偏移量'),
    data_service: DataService = Depends(get_data_service),
) -> List[CommandLog]:
    """查询命令日志。"""
    direction_enum = _parse_direction(direction)
    return data_service.list_command_logs(direction=direction_enum, limit=limit, offset=offset)


@router.post('/commands', status_code=status.HTTP_202_ACCEPTED)
def send_command(
    request: CommandRequest,
    http_request: Request,
    data_service: DataService = Depends(get_data_service),
) -> dict:
    """发送命令并记录日志。"""
    payload_text = _normalize_command_payload(request.payload)
    notes = request.notes or ''
    if request.request_id:
        notes = (notes + f' request_id={request.request_id}').strip()

    log = data_service.add_command_log(
        timestamp=datetime.now(),
        direction=CommandDirection.REQUEST,
        payload=payload_text,
        notes=notes or None,
        device_id=request.device_id,
    )
    if request.request_id:
        data_service.upsert_command_request(
            request_id=request.request_id,
            command_type='generic',
            device_id=request.device_id,
            request_payload=payload_text,
            status=CommandStatus.SENT,
        )

    mqtt_published = False
    mqtt_manager = getattr(http_request.app.state, 'mqtt', None)
    if mqtt_manager:
        payload_bytes = _hex_to_bytes(payload_text)
        topic = MQTT_TOPICS['command_request']
        mqtt_published = mqtt_manager.publish(topic, payload=payload_bytes)
        if not mqtt_published:
            logger.warning('Failed to publish command to MQTT topic %s', topic)
    else:
        logger.debug('MQTT manager not available on application state')

    return {
        'status': 'sent',
        'log_id': log.id,
        'mqtt_published': mqtt_published,
        'request_id': request.request_id,
    }


@router.get('/cableway/status', response_model=List[CablewayStatusRead])
def list_cableway_status(
    start: Optional[str] = Query(default=None, description='起始时间，ISO8601'),
    end: Optional[str] = Query(default=None, description='结束时间，ISO8601'),
    device_id: Optional[str] = Query(default=None, description='网关设备编号'),
    limit: Optional[int] = Query(default=50, ge=1, le=1000, description='返回数量上限'),
    offset: int = Query(default=0, ge=0, description='分页偏移量'),
    data_service: DataService = Depends(get_data_service),
) -> List[CablewayStatus]:
    """查询索道 PLC 状态历史。"""
    if limit == 1 and offset == 0 and start is None and end is None:
        cached = get_cached_cableway_status()
        if cached and (device_id is None or device_id == cached.get('device_id')):
            return [CablewayStatusRead(**cached)]
    start_at = _parse_datetime(start, 'start')
    end_at = _parse_datetime(end, 'end')
    return data_service.list_cableway_status(
        start=start_at,
        end=end_at,
        device_id=device_id,
        limit=limit,
        offset=offset,
    )


@router.get('/cableway/status/latest', response_model=CablewayStatusRead)
def get_latest_cableway_status(
    device_id: Optional[str] = Query(default=None, description='网关设备编号'),
    data_service: DataService = Depends(get_data_service),
) -> CablewayStatus:
    """获取最新索道 PLC 状态。"""
    cached = get_cached_cableway_status()
    if cached and (device_id is None or cached.get('device_id') == device_id):
        return CablewayStatusRead(**cached)
    entity = data_service.get_latest_cableway_status(device_id=device_id)
    if not entity:
        raise HTTPException(status_code=404, detail='Cableway status not found')
    return entity


@router.post('/cableway/command', status_code=status.HTTP_202_ACCEPTED)
def send_cableway_command(
    request: CablewayCommandRequest,
    http_request: Request,
    data_service: DataService = Depends(get_data_service),
) -> dict:
    """通过 MQTT 转发索道 PLC 指令/参数到网关执行。"""
    command_type = (request.type or '').strip()
    if not command_type:
        raise HTTPException(status_code=400, detail='type is required')

    plc_logger.info(
        "Cableway command request type=%s device_id=%s request_id=%s",
        command_type,
        request.device_id,
        request.request_id,
    )
    pulse_enabled = True if request.pulse is None else bool(request.pulse)
    payload: Dict[str, object] = {
        'schema': 1,
        'type': command_type,
        'timestamp': datetime.now().isoformat(timespec='seconds'),
        'pulse': pulse_enabled,
    }
    if request.request_id:
        payload['request_id'] = request.request_id
    if request.device_id:
        payload['device_id'] = request.device_id

    if command_type == 'control':
        if request.command_code is None:
            raise HTTPException(status_code=400, detail='command_code is required for control')
        try:
            payload['command_code'] = validate_control_command_code(request.command_code)
        except ValueError as exc:
            plc_logger.warning(
                "Cableway command validation failed code=%s error=%s",
                request.command_code,
                exc,
            )
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    elif command_type == 'estop':
        # Fixed payload: write VW2410=1 then reset to 0 (pulse on the gateway).
        pass
    elif command_type == 'set_params':
        try:
            payload['params'] = validate_param_updates(request.params or {})
        except ValueError as exc:
            plc_logger.warning(
                "Cableway command validation failed params=%s error=%s",
                request.params,
                exc,
            )
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    elif command_type == 'read_status':
        payload.pop('pulse', None)
    else:
        raise HTTPException(status_code=400, detail=f'Unsupported type: {command_type}')

    payload_text = json.dumps(payload, ensure_ascii=False)
    plc_logger.debug("Cableway command payload=%s", payload_text)
    notes = (request.notes or '').strip()
    if request.request_id:
        notes = (notes + f' request_id={request.request_id}').strip()
    notes = (notes + f' type={command_type}').strip()

    log = data_service.add_command_log(
        timestamp=datetime.now(),
        direction=CommandDirection.REQUEST,
        payload=payload_text,
        notes=notes or None,
        device_id=request.device_id,
    )
    if request.request_id:
        data_service.upsert_command_request(
            request_id=request.request_id,
            command_type='cableway',
            device_id=request.device_id,
            request_payload=payload_text,
            status=CommandStatus.SENT,
        )

    mqtt_published = False
    mqtt_manager = getattr(http_request.app.state, 'mqtt', None)
    if mqtt_manager:
        topic = MQTT_TOPICS['cableway_command_request']
        try:
            mqtt_published = mqtt_manager.publish(topic, payload=payload_text.encode('utf-8'))
        except Exception as exc:
            mqtt_published = False
            plc_logger.exception(
                "Cableway command publish failed topic=%s error=%s",
                topic,
                exc,
            )
        if not mqtt_published:
            plc_logger.warning('Failed to publish cableway command to MQTT topic %s', topic)
        else:
            plc_logger.info('Cableway command published topic=%s', topic)
    else:
        plc_logger.warning('MQTT manager not available for cableway command')

    return {
        'status': 'sent',
        'log_id': log.id,
        'mqtt_published': mqtt_published,
        'request_id': request.request_id,
        'type': command_type,
    }


@router.get('/command-requests', response_model=List[CommandRequestStatusRead])
def list_command_requests(
    status: Optional[str] = Query(default=None, description='命令状态 sent/ack/failed/timeout'),
    device_id: Optional[str] = Query(default=None, description='设备编号'),
    limit: Optional[int] = Query(default=50, ge=1, le=1000, description='返回数量上限'),
    offset: int = Query(default=0, ge=0, description='分页偏移量'),
    data_service: DataService = Depends(get_data_service),
) -> List[CommandRequestState]:
    """查询命令请求状态。"""
    status_enum = _parse_command_status(status)
    return data_service.list_command_requests(
        status=status_enum,
        device_id=device_id,
        limit=limit,
        offset=offset,
    )


@router.get('/command-requests/{request_id}', response_model=CommandRequestStatusRead)
def get_command_request(
    request_id: str,
    session: Session = Depends(get_session),
) -> CommandRequestState:
    """获取单条命令请求状态。"""
    entity = session.get(CommandRequestState, request_id)
    if not entity:
        raise HTTPException(status_code=404, detail='Command request not found')
    return entity


@router.get('/runtime-config')
def get_runtime_config(
    session: Session = Depends(get_session),
) -> Dict[str, object]:
    """返回运行期配置与覆盖结果。"""
    settings = get_settings()
    env_entries = load_env_entries(_ENV_PATH)
    base = _build_base_env_config(settings, env_entries)
    overrides = load_runtime_overrides(session)
    merged = merge_runtime_overrides(base, overrides)
    option_meta = _collect_option_meta(settings, env_entries)
    pending_overrides: Dict[str, object] = {}
    for key, value in (overrides or {}).items():
        meta = option_meta.get(key, {'type': 'text'})
        if _normalize_current_value(base.get(key), meta) != _normalize_current_value(value, meta):
            pending_overrides[key] = value
    hot_keys, restart_keys = split_runtime_override_keys(pending_overrides)
    return {
        'config': merged,
        'overrides': overrides,
        'hot_update_keys': hot_keys,
        'pending_restart_keys': restart_keys,
    }


@router.patch('/runtime-config')
def update_runtime_config(
    request: Request,
    payload: Dict[str, object] = Body(default_factory=dict),
    session: Session = Depends(get_session),
) -> Dict[str, object]:
    """更新运行期覆盖配置。"""
    debug_context: Dict[str, Any] = {'payload': payload}
    try:
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail='Payload must be a JSON object')
        settings = get_settings()
        env_entries = load_env_entries(_ENV_PATH)
        option_meta = _collect_option_meta(settings, env_entries)
        current_base = _build_base_env_config(settings, env_entries)
        current = merge_runtime_overrides(current_base, load_runtime_overrides(session))

        normalized_payload: Dict[str, object] = {}
        for key, value in (payload or {}).items():
            if not key:
                continue
            meta = option_meta.get(key, {'type': 'text', 'source': 'env'})
            normalized_payload[key] = _normalize_payload_value(key, value, meta)
        debug_context['normalized_payload'] = normalized_payload

        changed: Dict[str, object] = {}
        for key, value in normalized_payload.items():
            meta = option_meta.get(key, {'type': 'text'})
            if _normalize_current_value(current.get(key), meta) != _normalize_current_value(value, meta):
                changed[key] = value
        debug_context['changed'] = changed

        if not changed:
            return {
                'updated': False,
                'hot_update_keys': [],
                'pending_restart_keys': [],
                'restart_scheduled': False,
            }

        update_env_file(_ENV_PATH, changed)
        for key, value in changed.items():
            os.environ[key] = serialize_env_value(value)

        save_runtime_overrides(session, normalized_payload)

        get_settings.cache_clear()
        settings = get_settings()
        request.app.state.settings = settings

        changed_keys = list(changed.keys())
        debug_context['changed_keys'] = changed_keys

        if any(str(key).upper().startswith('MYSQL_') for key in changed_keys):
            rebuild_engine()

        mqtt_manager = getattr(request.app.state, 'mqtt', None)
        if mqtt_manager and any(
            str(key).upper().startswith('MQTT_') or str(key).upper() == 'APP_NAME'
            for key in changed_keys
        ):
            mqtt_manager.apply_settings(settings)

        yolo_service = getattr(request.app.state, 'yolo', None)
        if yolo_service and any(str(key).upper().startswith('YOLO_') for key in changed_keys):
            yolo_service.apply_settings(settings)

        audio_service = getattr(request.app.state, 'audio', None)
        if audio_service and any(str(key).upper().startswith('AUDIO_') for key in changed_keys):
            audio_service.apply_settings(settings)

        supervisor = getattr(request.app.state, 'supervisor', None)
        if supervisor:
            supervisor.apply_settings(settings, changed_keys=changed_keys)

        hot_keys = [key for key in changed_keys if is_hot_update_key(key)]
        restart_keys = [key for key in changed_keys if not is_hot_update_key(key)]
        restart_scheduled = bool(restart_keys)
        if restart_scheduled:
            _schedule_restart(settings, delay_seconds=1.0)

        return {
            'updated': True,
            'hot_update_keys': hot_keys,
            'pending_restart_keys': restart_keys,
            'restart_scheduled': restart_scheduled,
            'changed_keys': changed_keys,
        }
    except HTTPException:
        raise
    except Exception as exc:
        debug_context['error'] = str(exc)
        debug_context['traceback'] = traceback.format_exc()
        _write_runtime_config_debug(debug_context)
        logger.exception('runtime-config update failed')
        raise HTTPException(status_code=500, detail=f'runtime-config update failed: {exc}') from exc


@router.get('/runtime-config/options')
def get_runtime_config_options() -> Dict[str, object]:
    """返回运行期配置项说明。"""
    settings = get_settings()
    env_entries = load_env_entries(_ENV_PATH)
    sections = _build_runtime_config_sections(settings, env_entries)
    return {'sections': sections}


@router.get('/config/sensors', response_model=List[SensorConfigRead])
def list_sensor_configs(
    data_service: DataService = Depends(get_data_service),
) -> List[SensorConfigModel]:
    """列出传感器配置。"""
    return data_service.list_sensor_configs()


@router.post('/config/sensors', response_model=SensorConfigRead, status_code=status.HTTP_201_CREATED)
def create_sensor_config(
    payload: SensorConfigCreate,
    data_service: DataService = Depends(get_data_service),
) -> SensorConfigModel:
    """新增或更新传感器配置。"""
    entity = SensorConfigModel(**payload.dict())
    return data_service.upsert_sensor_config(entity)


@router.put('/config/sensors/{sensor_type}', response_model=SensorConfigRead)
def update_sensor_config(
    sensor_type: str,
    payload: SensorConfigCreate,
    data_service: DataService = Depends(get_data_service),
) -> SensorConfigModel:
    """更新指定传感器配置。"""
    if payload.type != sensor_type:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Payload type does not match path parameter',
        )
    entity = SensorConfigModel(**payload.dict())
    return data_service.upsert_sensor_config(entity)


@router.delete('/config/sensors/{sensor_type}', status_code=status.HTTP_204_NO_CONTENT)
def delete_sensor_config(
    sensor_type: str,
    data_service: DataService = Depends(get_data_service),
) -> None:
    """删除指定传感器配置。"""
    deleted = data_service.delete_sensor_config(sensor_type)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Config not found')


@router.get('/alarms', response_model=List[AlarmRecordRead])
def list_alarm_records(
    start: Optional[str] = Query(default=None, description='起始时间，ISO8601'),
    end: Optional[str] = Query(default=None, description='结束时间，ISO8601'),
    handled: Optional[str] = Query(default=None, description='是否已处理'),
    limit: Optional[int] = Query(default=None, ge=1, le=1000, description='返回数量上限'),
    offset: int = Query(default=0, ge=0, description='分页偏移量'),
    data_service: DataService = Depends(get_data_service),
) -> List[AlarmRecord]:
    """查询报警记录。"""
    if (
        limit == 1
        and offset == 0
        and start is None
        and end is None
        and handled is None
    ):
        cached = get_latest_alarm()
        if cached:
            return [AlarmRecordRead(**cached)]
    start_at = _parse_datetime(start, 'start')
    end_at = _parse_datetime(end, 'end')
    handled_flag = _parse_bool(handled, 'handled')
    return data_service.list_alarm_records(
        start=start_at,
        end=end_at,
        handled=handled_flag,
        limit=limit,
        offset=offset,
    )


@router.post('/alarms', response_model=AlarmRecordRead, status_code=status.HTTP_201_CREATED)
def create_alarm_record_api(
    payload: AlarmRecordCreate,
    http_request: Request,
    data_service: DataService = Depends(get_data_service),
) -> AlarmRecord:
    """创建报警记录。"""
    timestamp = payload.timestamp or datetime.now()
    entity = AlarmRecord(
        timestamp=timestamp,
        sensor_key=payload.sensor_key,
        sensor_name=payload.sensor_name,
        value=payload.value,
        unit=payload.unit,
        min_threshold=payload.min_threshold,
        max_threshold=payload.max_threshold,
        alarm_type=payload.alarm_type,
        is_handled=payload.is_handled,
        location=payload.location or '',
    )
    stored = data_service.create_alarm_record(entity)

    event = build_alarm_event(
        source='api_alarm',
        timestamp=stored.timestamp,
        device_id=payload.device_id,
        location=stored.location,
        payload={
            'alarm_id': stored.id,
            'sensor_key': stored.sensor_key,
            'sensor_name': stored.sensor_name,
            'value': float(stored.value),
            'unit': stored.unit,
            'min_threshold': float(stored.min_threshold),
            'max_threshold': float(stored.max_threshold),
            'alarm_type': stored.alarm_type.value,
            'is_handled': bool(stored.is_handled),
        },
    )
    mqtt_manager = getattr(http_request.app.state, 'mqtt', None)
    publish_alarm_event(mqtt_manager, event)
    return stored


@router.get('/images', response_model=List[ImageDataRead])
def list_image_data(
    start: Optional[str] = Query(default=None, description='起始时间，ISO8601'),
    end: Optional[str] = Query(default=None, description='结束时间，ISO8601'),
    device_id: Optional[str] = Query(default=None, description='设备编号'),
    location: Optional[str] = Query(default=None, description='安装位置'),
    limit: Optional[int] = Query(default=None, ge=1, le=1000, description='返回数量上限'),
    offset: int = Query(default=0, ge=0, description='分页偏移量'),
    data_service: DataService = Depends(get_data_service),
) -> List[ImageDataRead]:
    """查询图像数据。"""
    start_at = _parse_datetime(start, 'start')
    end_at = _parse_datetime(end, 'end')
    rows = data_service.list_image_data(
        start=start_at,
        end=end_at,
        device_id=device_id,
        location=location,
        limit=limit,
        offset=offset,
    )
    return [
        ImageDataRead(
            id=row.id or 0,
            timestamp=row.timestamp,
            device_id=row.device_id,
            image_name=row.image_name,
            image_data=_image_db_value_to_base64(row.image_data, row.image_path),
            location=row.location,
        )
        for row in rows
    ]


@router.post('/images', response_model=List[ImageDataRead], status_code=status.HTTP_201_CREATED)
def create_image_data(
    payload: Union[ImageDataCreate, List[ImageDataCreate]],
    data_service: DataService = Depends(get_data_service),
) -> List[ImageData]:
    """写入图像数据。"""
    items = payload if isinstance(payload, list) else [payload]
    entities = [ImageData(**item.dict()) for item in items]
    latest_rfid = data_service.get_latest_rfid_card()
    if latest_rfid:
        for entity in entities:
            entity.location = latest_rfid
    return data_service.create_image_data(entities)


@router.get('/audio', response_model=List[AudioDataRead])
def list_audio_data(
    start: Optional[str] = Query(default=None, description='起始时间，ISO8601'),
    end: Optional[str] = Query(default=None, description='结束时间，ISO8601'),
    device_id: Optional[str] = Query(default=None, description='设备编号'),
    location: Optional[str] = Query(default=None, description='安装位置'),
    limit: Optional[int] = Query(default=None, ge=1, le=1000, description='返回数量上限'),
    offset: int = Query(default=0, ge=0, description='分页偏移量'),
    data_service: DataService = Depends(get_data_service),
) -> List[AudioDataRead]:
    """查询音频数据。"""
    start_at = _parse_datetime(start, 'start')
    end_at = _parse_datetime(end, 'end')
    rows = data_service.list_audio_data(
        start=start_at,
        end=end_at,
        device_id=device_id,
        location=location,
        limit=limit,
        offset=offset,
    )
    return [
        AudioDataRead(
            id=row.id or 0,
            timestamp=row.timestamp,
            device_id=row.device_id,
            audio_name=row.audio_name,
            audio_data=_audio_db_value_to_base64(row.audio_data, row.audio_path),
            location=row.location,
        )
        for row in rows
    ]


@router.post('/audio', response_model=List[AudioDataRead], status_code=status.HTTP_201_CREATED)
def create_audio_data(
    payload: Union[AudioDataCreate, List[AudioDataCreate]],
    data_service: DataService = Depends(get_data_service),
) -> List[AudioDataRead]:
    """写入音频数据。"""
    items = payload if isinstance(payload, list) else [payload]
    entities = [
        AudioData(
            timestamp=item.timestamp,
            device_id=item.device_id,
            audio_name=item.audio_name,
            audio_data=_decode_audio_base64(item.audio_data),
            location=item.location,
        )
        for item in items
    ]
    latest_rfid = data_service.get_latest_rfid_card()
    if latest_rfid:
        for entity in entities:
            entity.location = latest_rfid
    stored = data_service.create_audio_data(entities)
    return [
        AudioDataRead(
            id=row.id or 0,
            timestamp=row.timestamp,
            device_id=row.device_id,
            audio_name=row.audio_name,
            audio_data=_audio_db_value_to_base64(row.audio_data, row.audio_path),
            location=row.location,
        )
        for row in stored
    ]


@router.get('/audio/metrics')
def list_audio_metrics(
    limit: int = Query(default=100, ge=1, le=500, description='返回近期特征数量'),
    audio_service: AudioMonitorService = Depends(get_audio_service),
) -> List[Dict[str, float]]:
    """返回近期计算的音频特征序列。"""
    return audio_service.get_metrics(limit=limit)


@router.post('/audio/thresholds')
def update_audio_thresholds(
    request: Request,
    payload: Dict[str, float],
    audio_service: AudioMonitorService = Depends(get_audio_service),
    session: Session = Depends(get_session),
) -> Dict[str, float]:
    """更新音频特征阈值并持久化。"""
    audio_service.update_thresholds(**payload)
    data = {}
    for key in ('centroid', 'bandwidth', 'rolloff', 'flatness', 'flux', 'rms'):
        val = payload.get(key)
        if val is None and f'audio_threshold_{key}' in payload:
            val = payload.get(f'audio_threshold_{key}')
        data[key] = float(val) if val is not None else 0.0

    record = AudioThreshold(
        centroid=data['centroid'],
        bandwidth=data['bandwidth'],
        rolloff=data['rolloff'],
        flatness=data['flatness'],
        flux=data['flux'],
        rms=data['rms'],
    )
    session.add(record)
    session.commit()
    settings = get_settings()
    supervisor = getattr(request.app.state, 'supervisor', None)
    if supervisor and (settings.audio_run_mode or 'thread').lower() == 'process':
        supervisor.restart_audio_worker(reason='audio thresholds updated')
    return data


@router.get('/audio/metrics/stream')
def stream_audio_metrics(
    audio_service: AudioMonitorService = Depends(get_audio_service),
) -> Dict[str, float]:
    """返回最新一帧音频特征（实时窗口）。"""
    metrics = audio_service.get_metrics(limit=1)
    return metrics[-1] if metrics else {}


@router.get('/metal-anomaly', response_model=List[MetalAnomalyRead])
def list_metal_anomaly(
    start: Optional[str] = Query(default=None, description='起始时间，ISO8601'),
    end: Optional[str] = Query(default=None, description='结束时间，ISO8601'),
    device_id: Optional[str] = Query(default=None, description='设备编号'),
    location: Optional[str] = Query(default=None, description='安装位置'),
    limit: Optional[int] = Query(default=None, ge=1, le=1000, description='返回数量上限'),
    offset: int = Query(default=0, ge=0, description='分页偏移量'),
    data_service: DataService = Depends(get_data_service),
) -> List[MetalAnomaly]:
    """查询金属异常记录。"""
    start_at = _parse_datetime(start, 'start')
    end_at = _parse_datetime(end, 'end')
    return data_service.list_metal_anomaly(
        start=start_at,
        end=end_at,
        device_id=device_id,
        location=location,
        limit=limit,
        offset=offset,
    )


@router.post('/metal-anomaly', response_model=List[MetalAnomalyRead], status_code=status.HTTP_201_CREATED)
def create_metal_anomaly(
    payload: Union[MetalAnomalyCreate, List[MetalAnomalyCreate]],
    data_service: DataService = Depends(get_data_service),
) -> List[MetalAnomaly]:
    """写入金属异常记录。"""
    items = payload if isinstance(payload, list) else [payload]
    entities = [MetalAnomaly(**item.dict()) for item in items]
    return data_service.create_metal_anomaly(entities)
