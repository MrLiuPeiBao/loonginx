"""REST API routes."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import sys
import threading
import time
import traceback
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request as UrlRequest, urlopen
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union, TYPE_CHECKING, get_args, get_origin

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse

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
from app.db.session import db_ping, rebuild_engine
from app.mqtt import MQTTMessageContext
from app.schemas.alarm import AlarmRecordCreate, AlarmRecordPage, AlarmRecordRead
from app.schemas.audio import AudioDataCreate, AudioDataPage, AudioDataRead
from app.db.audio_thresholds import AudioThreshold
from app.schemas.bms import BMSDataCreate, BMSDataPage, BMSDataRead
from app.schemas.cableway import CablewayCommandRequest, CablewayStatusPage, CablewayStatusRead
from app.schemas.command import (
    CommandLogPage,
    CommandLogRead,
    CommandRequest,
    CommandRequestStatusPage,
    CommandRequestStatusRead,
)
from app.schemas.config import SensorConfigCreate, SensorConfigRead
from app.schemas.image import ImageDataCreate, ImageDataPage, ImageDataRead
from app.schemas.metal import MetalAnomalyCreate, MetalAnomalyPage, MetalAnomalyRead
from app.schemas.rfid import RFIDDataCreate, RFIDDataPage, RFIDDataRead
from app.schemas.sensor import SensorDataCreate, SensorDataPage, SensorDataRead
from app.services.data_service import DataService
from app.services.alarm_publisher import build_alarm_event, publish_alarm_event_async
from app.services.bms_alerts import maybe_create_bms_low_voltage_alarm
from app.services.bms_cache import set_latest_bms
from app.services.cableway_cache import get_latest_cableway_status as get_cached_cableway_status
from app.services.cableway_ingestion import handle_cableway_status_payload
from app.services.cableway_specs import validate_control_command_code
from app.services.command_ingestion import handle_command_response_payload
from app.services.config_sync import publish_threshold_config_update
from app.services.media_storage import load_file_base64
from app.services.plc_timing import emit_plc_timing, new_trace_id
from app.runtime import PLCBridgeError
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
DEFAULT_QUERY_LIMIT = 200
DEFAULT_QUERY_LOOKBACK_HOURS = 24
_ENV_PATH = Path(__file__).resolve().parents[2] / '.env'
_BOOL_TRUE_VALUES = {'true', '1', 'yes', 'on'}
_BOOL_FALSE_VALUES = {'false', '0', 'no', 'off'}


def _forwarded_host_with_port(request: Request) -> str:
    host = (request.headers.get('x-forwarded-host') or request.headers.get('host') or '').strip()
    port = (request.headers.get('x-forwarded-port') or '').strip()
    if port and ':' not in host:
        host = f'{host}:{port}'
    return host


def get_data_service(request: Request):
    """Return an async DataService proxy routed to read/write DB workers."""
    read_db_worker = getattr(request.app.state, 'read_db_worker', None) or getattr(request.app.state, 'db_worker', None)
    write_db_worker = getattr(request.app.state, 'write_db_worker', None) or getattr(request.app.state, 'db_worker', None)
    if read_db_worker is not None and write_db_worker is not None:
        from app.runtime import RoutedAsyncDataServiceProxy

        return RoutedAsyncDataServiceProxy(read_worker=read_db_worker, write_worker=write_db_worker)
    raise HTTPException(status_code=503, detail='DB read/write workers not available')

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
    'media': {'title': '媒体（存储/网关）', 'description': ''},
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
    'MEDIA_GATEWAY_RUN_MODE': ['process'],
    'MEDIA_GATEWAY_TYPE': ['custom', 'mediamtx', 'go2rtc', 'none'],
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
    try:
        from main import app as current_app

        supervisor = getattr(current_app.state, 'supervisor', None)
        if supervisor:
            supervisor.cleanup_before_exec_restart()
    except Exception:
        logger.debug('Failed to cleanup supervisor before restart', exc_info=True)

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


def _image_read_from_row(row: ImageData, *, include_data: bool = True) -> ImageDataRead:
    return ImageDataRead(
        id=row.id or 0,
        timestamp=row.timestamp,
        device_id=row.device_id,
        image_name=row.image_name,
        image_data=_image_db_value_to_base64(row.image_data, row.image_path) if include_data else '',
        location=row.location,
    )


def _audio_read_from_row(row: AudioData, *, include_data: bool = True) -> AudioDataRead:
    return AudioDataRead(
        id=row.id or 0,
        timestamp=row.timestamp,
        device_id=row.device_id,
        audio_name=row.audio_name,
        audio_data=_audio_db_value_to_base64(row.audio_data, row.audio_path) if include_data else '',
        location=row.location,
    )


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


async def _raise_command_dispatch_failure(
    *,
    data_service: DataService,
    request_id: Optional[str],
    device_id: Optional[str],
    detail: str,
) -> None:
    if request_id:
        try:
            await data_service.update_command_request_status(
                request_id=request_id,
                status=CommandStatus.FAILED,
                error=detail,
                device_id=device_id,
            )
        except Exception:
            logger.debug('Skip command failure persistence because DB is unavailable', exc_info=True)
    raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=detail)


def _build_mqtt_json_context(topic: str, payload: Dict[str, Any]) -> MQTTMessageContext:
    return MQTTMessageContext(
        topic=topic,
        payload=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
        qos=0,
        retain=False,
    )


def _get_plc_bridge(http_request: Request):
    bridge = getattr(http_request.app.state, 'plc_bridge', None)
    if bridge is None:
        raise HTTPException(status_code=503, detail='PLC bridge not available')
    return bridge


def _get_read_db_worker(request: Request):
    worker = getattr(request.app.state, 'read_db_worker', None) or getattr(request.app.state, 'db_worker', None)
    if worker is None:
        raise HTTPException(status_code=503, detail='DB read worker not available')
    return worker


def _get_write_db_worker(request: Request):
    worker = getattr(request.app.state, 'write_db_worker', None) or getattr(request.app.state, 'db_worker', None)
    if worker is None:
        raise HTTPException(status_code=503, detail='DB write worker not available')
    return worker


def _get_db_worker(request: Request):
    # Backward-compatible alias; default to write-path ownership for direct worker access.
    return _get_write_db_worker(request)


def _read_db_worker_available(request: Request) -> bool:
    return bool(getattr(request.app.state, 'read_db_worker', None) or getattr(request.app.state, 'db_worker', None))


def _write_db_worker_available(request: Request) -> bool:
    return bool(getattr(request.app.state, 'write_db_worker', None) or getattr(request.app.state, 'db_worker', None))


def _get_media_db_worker(request: Request):
    worker = getattr(request.app.state, 'media_db_worker', None) or getattr(request.app.state, 'write_db_worker', None) or getattr(request.app.state, 'db_worker', None)
    if worker is None:
        raise HTTPException(status_code=503, detail='Media DB worker not available')
    return worker


def _get_media_data_service(request: Request):
    from app.runtime import AsyncDataServiceProxy

    return AsyncDataServiceProxy(_get_media_db_worker(request))


def _build_live_cableway_status_read(
    *,
    status: Dict[str, Any],
    device_id: str,
    location: str,
) -> CablewayStatusRead:
    timestamp_text = str(status.get('timestamp') or datetime.now().isoformat())
    try:
        timestamp = datetime.fromisoformat(timestamp_text)
    except ValueError:
        timestamp = datetime.now()
    return CablewayStatusRead(
        id=0,
        timestamp=timestamp,
        device_id=device_id,
        location=location,
        plc_host=str(status.get('plc_host') or ''),
        status=dict(status),
    )


def _apply_default_query_window(
    start_at: Optional[datetime],
    end_at: Optional[datetime],
    limit: Optional[int],
) -> tuple[Optional[datetime], Optional[datetime], int]:
    if start_at is None and end_at is None:
        start_at = datetime.now() - timedelta(hours=DEFAULT_QUERY_LOOKBACK_HOURS)
    return start_at, end_at, limit or DEFAULT_QUERY_LIMIT


def _sanitize_rtsp_source(url: str) -> str:
    text = str(url or '').strip()
    if not text:
        return ''
    try:
        parts = urlsplit(text)
    except Exception:
        return text
    host = parts.hostname or ''
    port = f':{parts.port}' if parts.port else ''
    return urlunsplit((parts.scheme or 'rtsp', f'{host}{port}', parts.path or '', '', ''))


def _parse_status_timestamp(value: object) -> Optional[datetime]:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)):
        try:
            parsed = datetime.fromtimestamp(float(value), tz=timezone.utc)
        except Exception:
            return None
    else:
        text = str(value or '').strip()
        if not text:
            return None
        if text.endswith('Z'):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None

    if parsed.tzinfo is not None:
        try:
            parsed = parsed.astimezone().replace(tzinfo=None)
        except Exception:
            parsed = parsed.replace(tzinfo=None)
    return parsed


def _is_recent_status_timestamp(value: object, *, max_age_seconds: float) -> bool:
    parsed = _parse_status_timestamp(value)
    if parsed is None:
        return False
    age_seconds = (datetime.now() - parsed).total_seconds()
    return 0.0 <= age_seconds <= float(max_age_seconds)


def _probe_go2rtc_stream_online(*, api_base: str, stream: str, timeout_seconds: float = 1.5) -> bool:
    base = str(api_base or '').strip().rstrip('/')
    src = str(stream or '').strip()
    if not base or not src:
        return False
    url = f'{base}/api/streams?src={src}&video=all&audio=all'
    request = UrlRequest(url=url, method='GET')
    try:
        with urlopen(request, timeout=max(0.2, float(timeout_seconds))) as response:
            return int(getattr(response, 'status', 0) or 0) == 200
    except HTTPError as exc:
        status_code = int(getattr(exc, 'code', 0) or 0)
        if status_code in {502, 503, 504}:
            return False
        logger.debug('go2rtc probe stream=%s status=%s', src, status_code)
        return False
    except URLError:
        return False
    except Exception:
        logger.debug('go2rtc probe failed stream=%s', src, exc_info=True)
        return False


def _normalize_refresh_limit(value: Optional[int], fallback: int) -> int:
    if value is None:
        return fallback
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    if parsed < 1:
        return fallback
    return min(parsed, 1000)


def _build_refresh_page(
    total: int,
    items: List[Any],
    *,
    include_data: bool = False,
    row_mapper: Optional[Callable[[Any], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    for row in items or []:
        if row_mapper is not None:
            mapped = row_mapper(row)
        elif hasattr(row, 'dict'):
            mapped = row.dict()  # type: ignore[assignment]
        else:
            mapped = dict(row)  # type: ignore[arg-type]
        if not include_data:
            if 'image_data' in mapped:
                mapped['image_data'] = ''
            if 'audio_data' in mapped:
                mapped['audio_data'] = ''
        rows.append(mapped)
    return {'total': int(total), 'items': rows}


async def _run_refresh_section(
    *,
    key: str,
    timeout_seconds: float,
    loader: Callable[[], Awaitable[Dict[str, Any]]],
) -> Dict[str, Any]:
    start_at = time.perf_counter()
    try:
        data = await asyncio.wait_for(loader(), timeout=timeout_seconds)
        elapsed_ms = int((time.perf_counter() - start_at) * 1000)
        return {
            'ok': True,
            'timeout': False,
            'error': None,
            'elapsed_ms': elapsed_ms,
            'data': data,
        }
    except asyncio.TimeoutError:
        elapsed_ms = int((time.perf_counter() - start_at) * 1000)
        logger.warning('GUI refresh section timeout key=%s timeout=%.1fs elapsed_ms=%s', key, timeout_seconds, elapsed_ms)
        return {
            'ok': False,
            'timeout': True,
            'error': f'{key} timeout',
            'elapsed_ms': elapsed_ms,
            'data': {'total': 0, 'items': []},
        }
    except HTTPException as exc:
        elapsed_ms = int((time.perf_counter() - start_at) * 1000)
        logger.warning('GUI refresh section http_error key=%s status=%s detail=%s', key, exc.status_code, exc.detail)
        return {
            'ok': False,
            'timeout': False,
            'error': f'{key} http_{exc.status_code}',
            'elapsed_ms': elapsed_ms,
            'data': {'total': 0, 'items': []},
        }
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - start_at) * 1000)
        logger.warning('GUI refresh section failed key=%s error=%s', key, exc)
        return {
            'ok': False,
            'timeout': False,
            'error': f'{key} failed: {exc}',
            'elapsed_ms': elapsed_ms,
            'data': {'total': 0, 'items': []},
        }


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
async def health(request: Request, settings: Settings = Depends(get_settings)) -> dict:
    """健康检查。"""
    mqtt_manager = getattr(request.app.state, 'mqtt', None)
    mqtt_ok = True if not settings.mqtt_enabled else bool(getattr(mqtt_manager, 'is_connected', False))
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
    media_write_worker = getattr(request.app.state, 'media_write_worker', None)
    if media_write_worker and hasattr(media_write_worker, 'get_status'):
        try:
            status_payload.update(media_write_worker.get_status())
        except Exception:
            logger.debug('Failed to read media writer status', exc_info=True)
    return {
        'status': 'ok',
        'app': settings.app_name,
        'timestamp': datetime.now().isoformat(timespec='seconds'),
        **status_payload,
    }


@router.get('/health/live')
async def health_live(settings: Settings = Depends(get_settings)) -> dict:
    """Liveness probe: process is running."""
    return {
        'status': 'ok',
        'app': settings.app_name,
        'timestamp': datetime.now().isoformat(timespec='seconds'),
    }


@router.get('/health/ready')
async def health_ready(request: Request, settings: Settings = Depends(get_settings)) -> JSONResponse:
    """Readiness probe: DB must be available, and MQTT only when enabled."""
    mqtt_manager = getattr(request.app.state, 'mqtt', None)
    mqtt_ok = True if not settings.mqtt_enabled else bool(getattr(mqtt_manager, 'is_connected', False))
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
    media_write_worker = getattr(request.app.state, 'media_write_worker', None)
    if media_write_worker and hasattr(media_write_worker, 'get_status'):
        try:
            payload.update(media_write_worker.get_status())
        except Exception:
            logger.debug('Failed to read media writer status', exc_info=True)

    if payload.get('db_ok') and payload.get('mqtt_ok'):
        return JSONResponse(status_code=200, content=payload)

    payload['status'] = 'not_ready'
    return JSONResponse(status_code=503, content=payload)


@router.get('/health/workers')
async def health_workers(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> Dict[str, object]:
    """Expose worker and RTSP source status for operations troubleshooting."""
    supervisor = getattr(request.app.state, 'supervisor', None)
    supervisor_status: Dict[str, object] = {}
    if supervisor and hasattr(supervisor, 'get_status'):
        try:
            status_payload = supervisor.get_status()
            if isinstance(status_payload, dict):
                supervisor_status = dict(status_payload)
        except Exception:
            logger.debug('Failed to read supervisor status', exc_info=True)

    yolo_input = str(getattr(settings, 'yolo_rtsp_input', '') or '').strip()
    audio_input = str(getattr(settings, 'audio_rtsp_input', '') or '').strip()
    yolo_effective_input = str(settings.resolve_yolo_rtsp_input() or '').strip()
    audio_effective_input = str(settings.resolve_audio_rtsp_input() or '').strip()
    control_panel_effective_input = str(settings.resolve_control_panel_rtsp_input() or '').strip()
    yolo_source = _sanitize_rtsp_source(yolo_effective_input)
    audio_source = _sanitize_rtsp_source(audio_effective_input)
    yolo_direct_source = _sanitize_rtsp_source(yolo_input)
    audio_direct_source = _sanitize_rtsp_source(audio_input)
    same_rtsp_source = bool(yolo_source and audio_source and yolo_source == audio_source)
    expected_consumers = 0
    if bool(getattr(settings, 'yolo_enabled', False)) and yolo_source:
        expected_consumers += 1
    if bool(getattr(settings, 'audio_enabled', False)) and audio_source:
        expected_consumers += 1
    forwarded_host = _forwarded_host_with_port(request).lower()
    api_base_url = str(getattr(settings, 'api_base_url', '') or '').strip().lower()
    control_panel_hint = (
        forwarded_host in {'127.0.0.1:8888', 'localhost:8888'}
        or api_base_url.endswith(':8888')
    )

    workers = {
        'yolo': {
            'enabled': bool(getattr(settings, 'yolo_enabled', False)),
            'run_mode': str(getattr(settings, 'yolo_run_mode', 'thread') or 'thread'),
            'process_alive': bool(supervisor_status.get('yolo_process_alive', False)),
            'rtsp_input_direct': yolo_direct_source,
            'rtsp_input': yolo_source,
            'watchdog': {},
        },
        'audio': {
            'enabled': bool(getattr(settings, 'audio_enabled', False)),
            'run_mode': str(getattr(settings, 'audio_run_mode', 'thread') or 'thread'),
            'process_alive': bool(supervisor_status.get('audio_process_alive', False)),
            'rtsp_input_direct': audio_direct_source,
            'rtsp_input': audio_source,
        },
        'media_gateway': {
            'enabled': bool(settings.is_media_gateway_enabled()),
            'type': str(getattr(settings, 'media_gateway_type', '') or ''),
            'run_mode': str(getattr(settings, 'media_gateway_run_mode', '') or ''),
            'process_alive': bool(supervisor_status.get('media_gateway_process_alive', False)),
            'relay_rtsp': _sanitize_rtsp_source(
                str(getattr(settings, 'media_gateway_relay_rtsp', '') or '').strip()
            ),
            'control_panel_rtsp': _sanitize_rtsp_source(control_panel_effective_input),
        },
    }
    yolo_service = getattr(request.app.state, 'yolo', None)
    if yolo_service and hasattr(yolo_service, 'get_watchdog_status'):
        try:
            watchdog_payload = yolo_service.get_watchdog_status()
            if isinstance(watchdog_payload, dict):
                workers['yolo']['watchdog'] = watchdog_payload
        except Exception:
            logger.debug('Failed to read yolo watchdog status', exc_info=True)
    rtsp = {
        'shared_source': same_rtsp_source,
        'expected_consumers': expected_consumers,
        'source': yolo_source if yolo_source == audio_source else '',
        'yolo_input_direct': yolo_direct_source,
        'audio_input_direct': audio_direct_source,
        'yolo_input_effective': yolo_source,
        'audio_input_effective': audio_source,
        'control_panel_input_effective': _sanitize_rtsp_source(control_panel_effective_input),
        'control_panel_possible_consumer': control_panel_hint,
        'gateway_rewrite_active': bool(
            settings.is_media_gateway_enabled()
            and (
                yolo_direct_source != yolo_source
                or audio_direct_source != audio_source
            )
        ),
        'note': (
            'Potential multi-consumer RTSP contention: YOLO/Audio/control-panel may pull the same camera directly'
            if same_rtsp_source and expected_consumers >= 2 and not settings.is_media_gateway_enabled()
            else (
                'Media gateway is enabled; keep control-panel on gateway RTSP to avoid direct multi-pull'
                if settings.is_media_gateway_enabled()
                else ''
            )
        ),
        'suggested_control_panel_rtsp': (
            _sanitize_rtsp_source(control_panel_effective_input)
            if settings.is_media_gateway_enabled()
            else ''
        ),
    }
    return {
        'status': 'ok',
        'app': settings.app_name,
        'timestamp': datetime.now().isoformat(timespec='seconds'),
        'workers': workers,
        'rtsp': rtsp,
        'supervisor': supervisor_status,
    }


@router.get('/runtime/media-status')
async def runtime_media_status(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> Dict[str, object]:
    """Runtime media plane status for control-panel degrade decisions."""
    supervisor = getattr(request.app.state, 'supervisor', None)
    supervisor_status: Dict[str, object] = {}
    if supervisor and hasattr(supervisor, 'get_status'):
        try:
            status_payload = supervisor.get_status()
            if isinstance(status_payload, dict):
                supervisor_status = dict(status_payload)
        except Exception:
            logger.debug('Failed to read supervisor status', exc_info=True)

    media_write_worker = getattr(request.app.state, 'media_write_worker', None)
    media_writer_status: Dict[str, object] = {}
    if media_write_worker and hasattr(media_write_worker, 'get_status'):
        try:
            status_payload = media_write_worker.get_status()
            if isinstance(status_payload, dict):
                media_writer_status = dict(status_payload)
        except Exception:
            logger.debug('Failed to read media writer status', exc_info=True)

    yolo_watchdog: Dict[str, Any] = {}
    yolo_service = getattr(request.app.state, 'yolo', None)
    if yolo_service and hasattr(yolo_service, 'get_watchdog_status'):
        try:
            status_payload = yolo_service.get_watchdog_status()
            if isinstance(status_payload, dict):
                yolo_watchdog = dict(status_payload)
        except Exception:
            logger.debug('Failed to read yolo watchdog status', exc_info=True)

    audio_service = getattr(request.app.state, 'audio', None)
    audio_metrics_count = 0
    audio_metrics_recent = False
    if audio_service and hasattr(audio_service, 'get_metrics'):
        try:
            metrics = audio_service.get_metrics(limit=1)
            if isinstance(metrics, list):
                audio_metrics_count = len(metrics)
                if metrics:
                    latest = metrics[-1] if isinstance(metrics[-1], dict) else {}
                    audio_metrics_recent = _is_recent_status_timestamp(
                        latest.get('timestamp'),
                        max_age_seconds=30.0,
                    )
        except Exception:
            logger.debug('Failed to read audio metrics status', exc_info=True)

    camera_source = _sanitize_rtsp_source(str(settings.resolve_control_panel_rtsp_input() or '').strip())
    yolo_input = _sanitize_rtsp_source(str(settings.resolve_yolo_rtsp_input() or '').strip())
    audio_input = _sanitize_rtsp_source(str(settings.resolve_audio_rtsp_input() or '').strip())
    yolo_output = _sanitize_rtsp_source(str(getattr(settings, 'yolo_rtsp_output', '') or '').strip())

    yolo_process_alive = bool(supervisor_status.get('yolo_process_alive', False))
    audio_process_alive = bool(supervisor_status.get('audio_process_alive', False))
    gateway_process_alive = bool(supervisor_status.get('media_gateway_process_alive', False))
    gateway_enabled = bool(settings.is_media_gateway_enabled())
    go2rtc_api_base = str(getattr(settings, 'media_gateway_http_api', '') or '').strip() or 'http://127.0.0.1:1984'
    relay_rtsp = str(getattr(settings, 'media_gateway_relay_rtsp', '') or '').strip()
    relay_stream_name = ''
    if relay_rtsp:
        try:
            relay_stream_name = (urlsplit(relay_rtsp).path or '').strip('/').split('/')[0]
        except Exception:
            relay_stream_name = ''
    go2rtc_stream_online = False
    if gateway_enabled and gateway_process_alive and relay_stream_name:
        go2rtc_stream_online = await asyncio.to_thread(
            _probe_go2rtc_stream_online,
            api_base=go2rtc_api_base,
            stream=relay_stream_name,
        )

    yolo_online = bool(
        getattr(settings, 'yolo_enabled', False)
        and (
            (str(getattr(settings, 'yolo_run_mode', 'thread') or 'thread').lower() == 'thread')
            or yolo_process_alive
        )
    )
    yolo_recent_frame = _is_recent_status_timestamp(
        yolo_watchdog.get('last_inference_finished_at'),
        max_age_seconds=30.0,
    )
    if getattr(settings, 'yolo_enabled', False):
        yolo_online = bool(yolo_online and yolo_recent_frame)
    audio_online = bool(
        getattr(settings, 'audio_enabled', False)
        and (
            (str(getattr(settings, 'audio_run_mode', 'thread') or 'thread').lower() == 'thread')
            or audio_process_alive
        )
    )
    if getattr(settings, 'audio_enabled', False):
        audio_online = bool(audio_online and audio_metrics_recent)
    go2rtc_online = bool(gateway_enabled and gateway_process_alive and go2rtc_stream_online)
    camera_online = bool(
        camera_source and (
            (gateway_enabled and go2rtc_online)
            or (not gateway_enabled and (yolo_online or audio_online))
        )
    )

    last_frame_at = yolo_watchdog.get('last_inference_finished_at')
    if not last_frame_at:
        last_frame_at = datetime.now().isoformat(timespec='seconds') if yolo_online else None

    return {
        'status': 'ok',
        'timestamp': datetime.now().isoformat(timespec='seconds'),
        'camera_online': camera_online,
        'go2rtc_online': go2rtc_online,
        'yolo_online': yolo_online,
        'audio_online': audio_online,
        'last_frame_at': last_frame_at,
        'media_queue_size': int(media_writer_status.get('media_queue_size', 0) or 0),
        'media_queue_capacity': int(media_writer_status.get('media_queue_capacity', 0) or 0),
        'media_writer_alive': bool(media_writer_status.get('media_writer_alive', False)),
        'audio_metrics_available': audio_metrics_count > 0,
        'rtsp': {
            'camera_source': camera_source,
            'yolo_input': yolo_input,
            'audio_input': audio_input,
            'yolo_output': yolo_output,
        },
    }


@router.get('/sensors', response_model=SensorDataPage)
async def list_sensor_data(
    start: Optional[str] = Query(default=None, description='起始时间，ISO8601'),
    end: Optional[str] = Query(default=None, description='结束时间，ISO8601'),
    device_id: Optional[str] = Query(default=None, description='设备编号'),
    location: Optional[str] = Query(default=None, description='安装位置'),
    limit: Optional[int] = Query(default=None, ge=1, le=1000, description='返回数量上限'),
    offset: int = Query(default=0, ge=0, description='分页偏移量'),
    data_service: DataService = Depends(get_data_service),
) -> SensorDataPage:
    """查询传感器数据。"""
    start_at = _parse_datetime(start, 'start')
    end_at = _parse_datetime(end, 'end')
    start_at, end_at, limit = _apply_default_query_window(start_at, end_at, limit)
    total = await data_service.count_sensor_data(
        start=start_at,
        end=end_at,
        device_id=device_id,
        location=location,
    )
    items = await data_service.list_sensor_data(
        start=start_at,
        end=end_at,
        device_id=device_id,
        location=location,
        limit=limit,
        offset=offset,
    )
    return SensorDataPage(total=total, items=items)


@router.post('/sensors', response_model=List[SensorDataRead], status_code=status.HTTP_201_CREATED)
async def create_sensor_data(
    payload: Union[SensorDataCreate, List[SensorDataCreate]],
    http_request: Request,
    data_service: DataService = Depends(get_data_service),
) -> List[SensorData]:
    """插入传感器数据。"""
    items = payload if isinstance(payload, list) else [payload]
    entities = [SensorData(**item.dict()) for item in items]
    latest_rfid = await data_service.get_latest_rfid_card()
    if latest_rfid:
        for entity in entities:
            entity.location = latest_rfid
    write_db_worker = _get_write_db_worker(http_request)
    stored, alarm_events = await write_db_worker.call_async(
        lambda session: (
            lambda service: (
                service.create_sensor_data(entities),
                service.consume_alarm_events(),
            )
        )(DataService(session))
    )
    if stored:
        latest = max(stored, key=lambda item: item.timestamp)
        set_latest_sensor(latest)
    mqtt_manager = getattr(http_request.app.state, 'mqtt', None)
    for event in alarm_events:
        await publish_alarm_event_async(mqtt_manager, event)
    return stored


@router.get('/sensors/latest', response_model=List[SensorDataRead])
async def latest_sensor_data(
    limit: int = Query(default=10, ge=1, le=100, description='返回最新的记录数量'),
    data_service: DataService = Depends(get_data_service),
) -> List[SensorData]:
    """查询最新的传感器数据。"""
    if limit == 1:
        cached = get_latest_sensor()
        if cached:
            return [SensorDataRead(**cached)]
    return await data_service.list_sensor_data(limit=limit)


@router.get('/bms', response_model=BMSDataPage)
async def list_bms_data(
    start: Optional[str] = Query(default=None, description='起始时间，ISO8601'),
    end: Optional[str] = Query(default=None, description='结束时间，ISO8601'),
    device_id: Optional[str] = Query(default=None, description='设备编号'),
    limit: Optional[int] = Query(default=None, ge=1, le=1000, description='返回数量上限'),
    offset: int = Query(default=0, ge=0, description='分页偏移量'),
    data_service: DataService = Depends(get_data_service),
) -> BMSDataPage:
    """查询 BMS 数据。"""
    start_at = _parse_datetime(start, 'start')
    end_at = _parse_datetime(end, 'end')
    start_at, end_at, limit = _apply_default_query_window(start_at, end_at, limit)
    total = await data_service.count_bms_data(
        start=start_at,
        end=end_at,
        device_id=device_id,
    )
    items = await data_service.list_bms_data(
        start=start_at,
        end=end_at,
        device_id=device_id,
        limit=limit,
        offset=offset,
    )
    return BMSDataPage(total=total, items=items)


@router.post('/bms', response_model=List[BMSDataRead], status_code=status.HTTP_201_CREATED)
async def create_bms_data(
    payload: Union[BMSDataCreate, List[BMSDataCreate]],
    http_request: Request,
    data_service: DataService = Depends(get_data_service),
) -> List[BMSData]:
    """插入 BMS 数据。"""
    items = payload if isinstance(payload, list) else [payload]
    entities = [BMSData(**item.dict()) for item in items]
    latest_rfid = await data_service.get_latest_rfid_card()
    if latest_rfid:
        for entity in entities:
            entity.location = latest_rfid
    stored = await data_service.create_bms_data(entities)
    if stored:
        latest = max(stored, key=lambda item: item.timestamp)
        set_latest_bms(latest)
    mqtt_manager = getattr(http_request.app.state, 'mqtt', None)
    write_db_worker = _get_write_db_worker(http_request)
    for entity in stored:
        alarm_event = await write_db_worker.call_async(
            lambda session, current=entity: maybe_create_bms_low_voltage_alarm(DataService(session), current)
        )
        if alarm_event:
            await publish_alarm_event_async(mqtt_manager, alarm_event)
    return stored


@router.get('/rfid', response_model=RFIDDataPage)
async def list_rfid_data(
    start: Optional[str] = Query(default=None, description='起始时间，ISO8601'),
    end: Optional[str] = Query(default=None, description='结束时间，ISO8601'),
    device_id: Optional[str] = Query(default=None, description='设备编号'),
    limit: Optional[int] = Query(default=None, ge=1, le=1000, description='返回数量上限'),
    offset: int = Query(default=0, ge=0, description='分页偏移量'),
    data_service: DataService = Depends(get_data_service),
) -> RFIDDataPage:
    """查询 RFID 数据。"""
    start_at = _parse_datetime(start, 'start')
    end_at = _parse_datetime(end, 'end')
    start_at, end_at, limit = _apply_default_query_window(start_at, end_at, limit)
    total = await data_service.count_rfid_data(
        start=start_at,
        end=end_at,
        device_id=device_id,
    )
    items = await data_service.list_rfid_data(
        start=start_at,
        end=end_at,
        device_id=device_id,
        limit=limit,
        offset=offset,
    )
    return RFIDDataPage(total=total, items=items)


@router.get('/rfid/latest', response_model=RFIDDataRead)
async def get_latest_rfid_data(
    device_id: Optional[str] = Query(default=None, description='设备编号'),
    data_service: DataService = Depends(get_data_service),
) -> RFIDData:
    """获取最新 RFID 数据。"""
    snapshot = get_latest_rfid()
    if snapshot and (device_id is None or snapshot.device_id == device_id):
        return RFIDDataRead(**asdict(snapshot))

    items = await data_service.list_rfid_data(device_id=device_id, limit=1)
    if not items:
        raise HTTPException(status_code=404, detail='RFID data not found')
    return items[0]


@router.post('/rfid', response_model=List[RFIDDataRead], status_code=status.HTTP_201_CREATED)
async def create_rfid_data(
    payload: Union[RFIDDataCreate, List[RFIDDataCreate]],
    data_service: DataService = Depends(get_data_service),
) -> List[RFIDData]:
    """插入 RFID 数据。"""
    items = payload if isinstance(payload, list) else [payload]
    entities = [RFIDData(**item.dict()) for item in items]
    stored = await data_service.create_rfid_data(entities)
    if stored:
        latest = max(stored, key=lambda item: item.timestamp)
        set_latest_rfid(latest)
    return stored


@router.get('/commands', response_model=CommandLogPage)
async def list_command_logs(
    direction: Optional[str] = Query(default=None, description='命令方向 request/response'),
    limit: Optional[int] = Query(default=50, ge=1, le=1000, description='返回数量上限'),
    offset: int = Query(default=0, ge=0, description='分页偏移量'),
    data_service: DataService = Depends(get_data_service),
) -> CommandLogPage:
    """查询命令日志。"""
    direction_enum = _parse_direction(direction)
    total = await data_service.count_command_logs(direction=direction_enum)
    items = await data_service.list_command_logs(direction=direction_enum, limit=limit, offset=offset)
    return CommandLogPage(total=total, items=items)


@router.post('/commands', status_code=status.HTTP_202_ACCEPTED)
async def send_command(
    request: CommandRequest,
    http_request: Request,
    data_service: DataService = Depends(get_data_service),
) -> dict:
    """发送命令并记录日志。"""
    payload_text = _normalize_command_payload(request.payload)
    notes = request.notes or ''
    if request.request_id:
        notes = (notes + f' request_id={request.request_id}').strip()

    log = await data_service.add_command_log(
        timestamp=datetime.now(),
        direction=CommandDirection.REQUEST,
        payload=payload_text,
        notes=notes or None,
        device_id=request.device_id,
    )
    if request.request_id:
        await data_service.upsert_command_request(
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
        try:
            mqtt_published = await mqtt_manager.publish_async(topic, payload=payload_bytes)
        except Exception as exc:
            logger.exception('Failed to publish command to MQTT topic %s: %s', topic, exc)
            await _raise_command_dispatch_failure(
                data_service=data_service,
                request_id=request.request_id,
                device_id=request.device_id,
                detail='Failed to publish command to MQTT broker',
            )
        if not mqtt_published:
            logger.warning('Failed to publish command to MQTT topic %s', topic)
            await _raise_command_dispatch_failure(
                data_service=data_service,
                request_id=request.request_id,
                device_id=request.device_id,
                detail='Failed to publish command to MQTT broker',
            )
    else:
        logger.debug('MQTT manager not available on application state')
        await _raise_command_dispatch_failure(
            data_service=data_service,
            request_id=request.request_id,
            device_id=request.device_id,
            detail='MQTT manager not available',
        )

    return {
        'status': 'sent',
        'log_id': log.id,
        'mqtt_published': mqtt_published,
        'request_id': request.request_id,
    }


@router.get('/cableway/status', response_model=CablewayStatusPage)
async def list_cableway_status(
    start: Optional[str] = Query(default=None, description='起始时间，ISO8601'),
    end: Optional[str] = Query(default=None, description='结束时间，ISO8601'),
    device_id: Optional[str] = Query(default=None, description='设备编号'),
    limit: Optional[int] = Query(default=50, ge=1, le=1000, description='返回数量上限'),
    offset: int = Query(default=0, ge=0, description='分页偏移量'),
    data_service: DataService = Depends(get_data_service),
) -> CablewayStatusPage:
    start_at = _parse_datetime(start, 'start')
    end_at = _parse_datetime(end, 'end')
    total = await data_service.count_cableway_status(
        start=start_at,
        end=end_at,
        device_id=device_id,
    )
    items = await data_service.list_cableway_status(
        start=start_at,
        end=end_at,
        device_id=device_id,
        limit=limit,
        offset=offset,
    )
    return CablewayStatusPage(total=total, items=items)


@router.get('/cableway/status/latest', response_model=CablewayStatusRead)
async def get_latest_cableway_status_api(
    http_request: Request,
    device_id: Optional[str] = Query(default=None, description='设备编号'),
    data_service: DataService = Depends(get_data_service),
) -> CablewayStatusRead:
    cached = get_cached_cableway_status()
    if isinstance(cached, dict):
        cached_status = cached.get('status')
        cached_device = str(cached.get('device_id') or '').strip()
        if isinstance(cached_status, dict) and (device_id is None or cached_device == device_id):
            return _build_live_cableway_status_read(
                status=cached_status,
                device_id=cached_device or device_id or 'server-plc',
                location=str(cached.get('location') or 'server'),
            )

    entity = await data_service.get_latest_cableway_status(device_id=device_id)
    if entity:
        return entity

    bridge = _get_plc_bridge(http_request)
    try:
        execution = await bridge.execute_command_async(command_type='read_status', device_id=device_id)
    except PLCBridgeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    result = execution.response_payload.get('result')
    if not isinstance(result, dict):
        raise HTTPException(status_code=404, detail='Cableway status not available')
    return _build_live_cableway_status_read(
        status=result,
        device_id=str(execution.response_payload.get('device_id') or device_id or 'server-plc'),
        location='server',
    )


@router.post('/cableway/command')
async def send_cableway_command(
    request: CablewayCommandRequest,
    http_request: Request,
    data_service: DataService = Depends(get_data_service),
) -> JSONResponse:
    raw_trace_id = str(http_request.headers.get('X-PLC-Trace-Id') or '').strip()
    trace_id = raw_trace_id or new_trace_id("plc")
    command_type = str(request.type or '').strip()
    base_fields: Dict[str, Any] = {
        'command_type': command_type,
        'command_code': request.command_code,
        'device_id': request.device_id,
        'db_available': _write_db_worker_available(http_request),
        'skip_db': False,
    }
    emit_plc_timing('api.route_enter', request_id=request.request_id, trace_id=trace_id, **base_fields)
    if not command_type:
        emit_plc_timing('api.response_ready', request_id=request.request_id, trace_id=trace_id, status_code=400, error='type is required', **base_fields)
        raise HTTPException(status_code=400, detail='type is required')

    command_code: Optional[int] = None
    if command_type == 'control':
        if request.command_code is None:
            emit_plc_timing('api.response_ready', request_id=request.request_id, trace_id=trace_id, status_code=400, error='command_code is required for control', **base_fields)
            raise HTTPException(status_code=400, detail='command_code is required for control')
        try:
            command_code = validate_control_command_code(request.command_code)
        except ValueError as exc:
            emit_plc_timing('api.response_ready', request_id=request.request_id, trace_id=trace_id, status_code=400, error=str(exc), **base_fields)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    elif command_type not in {'estop', 'read_status', 'query_faults'}:
        emit_plc_timing('api.response_ready', request_id=request.request_id, trace_id=trace_id, status_code=400, error=f'Unsupported type: {command_type}', **base_fields)
        raise HTTPException(status_code=400, detail=f'Unsupported type: {command_type}')

    request_id = str(request.request_id or '').strip() or f'plc-{int(time.time() * 1000)}'
    pulse_enabled = True if request.pulse is None else bool(request.pulse)
    payload: Dict[str, Any] = {
        'type': command_type,
        'request_id': request_id,
        'pulse': pulse_enabled,
    }
    if request.device_id:
        payload['device_id'] = request.device_id
    if command_code is not None:
        payload['command_code'] = command_code
    payload_text = json.dumps(payload, ensure_ascii=False)
    base_fields['command_code'] = command_code
    emit_plc_timing('api.request_parsed', request_id=request_id, trace_id=trace_id, **base_fields)
    emit_plc_timing('api.validation_done', request_id=request_id, trace_id=trace_id, **base_fields)

    notes = (request.notes or '').strip()
    if notes:
        notes = f'{notes} request_id={request_id}'
    else:
        notes = f'request_id={request_id}'

    emit_plc_timing('api.command_log_db_start', request_id=request_id, trace_id=trace_id, **base_fields)
    log = await data_service.add_command_log(
        timestamp=datetime.now(),
        direction=CommandDirection.REQUEST,
        payload=payload_text,
        notes=notes,
        device_id=request.device_id,
    )
    await data_service.upsert_command_request(
        request_id=request_id,
        command_type='cableway',
        device_id=request.device_id,
        request_payload=payload_text,
        status=CommandStatus.SENT,
    )
    emit_plc_timing('api.command_log_db_end', request_id=request_id, trace_id=trace_id, **base_fields)

    bridge = _get_plc_bridge(http_request)
    emit_plc_timing('api.execute_command_call_start', request_id=request_id, trace_id=trace_id, **base_fields)
    try:
        execution = await bridge.execute_command_async(
            command_type=command_type,
            request_id=request_id,
            trace_id=trace_id,
            device_id=request.device_id,
            command_code=command_code,
            pulse=pulse_enabled,
            params=request.params,
        )
    except PLCBridgeError as exc:
        emit_plc_timing('api.execute_command_call_end', request_id=request_id, trace_id=trace_id, error=str(exc), **base_fields)
        emit_plc_timing('api.response_ready', request_id=request_id, trace_id=trace_id, status_code=503, error=str(exc), **base_fields)
        await _raise_command_dispatch_failure(
            data_service=data_service,
            request_id=request_id,
            device_id=request.device_id,
            detail=str(exc),
        )
    emit_plc_timing('api.execute_command_call_end', request_id=request_id, trace_id=trace_id, **base_fields)

    response_payload = dict(execution.response_payload)
    response_text = json.dumps(response_payload, ensure_ascii=False)
    await data_service.add_command_log(
        timestamp=datetime.now(),
        direction=CommandDirection.RESPONSE,
        payload=response_text,
        notes=f'request_id={request_id}',
        device_id=request.device_id,
    )

    success = bool(response_payload.get('success'))
    await data_service.update_command_request_status(
        request_id=request_id,
        status=CommandStatus.ACK if success else CommandStatus.FAILED,
        response_payload=response_text,
        error=str(response_payload.get('error') or '') or None,
        device_id=request.device_id,
    )

    if execution.status_payload:
        mqtt_manager = getattr(http_request.app.state, 'mqtt', None)
        write_db_worker = _get_write_db_worker(http_request)
        await asyncio.to_thread(
            handle_cableway_status_payload,
            _build_mqtt_json_context(MQTT_TOPICS['cableway_status'], execution.status_payload),
            mqtt_manager,
            write_db_worker,
        )

    if not success:
        detail = str(response_payload.get('error') or 'PLC command failed')
        emit_plc_timing('api.response_ready', request_id=request_id, trace_id=trace_id, status_code=502, error=detail, **base_fields)
        raise HTTPException(status_code=502, detail=detail)

    emit_plc_timing('api.response_ready', request_id=request_id, trace_id=trace_id, status_code=200, **base_fields)
    return JSONResponse(
        content={
            'status': 'ack',
            'log_id': log.id,
            'request_id': request_id,
            'type': command_type,
            'response_payload': response_payload,
        },
        headers={
            'X-PLC-Request-Id': request_id,
            'X-PLC-Trace-Id': trace_id,
        },
    )


@router.get('/command-requests', response_model=CommandRequestStatusPage)
async def list_command_requests(
    status: Optional[str] = Query(default=None, description='命令状态 sent/ack/failed/timeout'),
    device_id: Optional[str] = Query(default=None, description='设备编号'),
    limit: Optional[int] = Query(default=50, ge=1, le=1000, description='返回数量上限'),
    offset: int = Query(default=0, ge=0, description='分页偏移量'),
    data_service: DataService = Depends(get_data_service),
) -> CommandRequestStatusPage:
    """查询命令请求状态。"""
    status_enum = _parse_command_status(status)
    total = await data_service.count_command_requests(
        status=status_enum,
        device_id=device_id,
    )
    items = await data_service.list_command_requests(
        status=status_enum,
        device_id=device_id,
        limit=limit,
        offset=offset,
    )
    return CommandRequestStatusPage(total=total, items=items)


@router.get('/command-requests/{request_id}', response_model=CommandRequestStatusRead)
async def get_command_request(
    request_id: str,
    request: Request,
) -> CommandRequestState:
    """获取单条命令请求状态。"""
    entity = await _get_read_db_worker(request).call_async(lambda session: session.get(CommandRequestState, request_id))
    if not entity:
        raise HTTPException(status_code=404, detail='Command request not found')
    return entity


@router.get('/runtime-config')
async def get_runtime_config(
    request: Request,
) -> Dict[str, object]:
    """返回运行期配置与覆盖结果。"""
    settings = get_settings()
    env_entries = load_env_entries(_ENV_PATH)
    base = _build_base_env_config(settings, env_entries)
    overrides = await _get_read_db_worker(request).call_async(load_runtime_overrides)
    merged = merge_runtime_overrides(base, overrides)
    forwarded_host = _forwarded_host_with_port(request)
    if forwarded_host.endswith(':8888'):
        merged['API_BASE_URL'] = f"http://{forwarded_host}"
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
async def update_runtime_config(
    request: Request,
    payload: Dict[str, object] = Body(default_factory=dict),
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
        read_db_worker = _get_read_db_worker(request)
        write_db_worker = _get_write_db_worker(request)
        current = merge_runtime_overrides(current_base, await read_db_worker.call_async(load_runtime_overrides))

        normalized_payload: Dict[str, object] = {}
        for key, value in (payload or {}).items():
            if not key:
                continue
            meta = option_meta.get(key, {'type': 'text', 'source': 'env'})
            normalized_payload[key] = _normalize_payload_value(key, value, meta)
        forwarded_host = _forwarded_host_with_port(request)
        incoming_api_base = str(normalized_payload.get('API_BASE_URL') or '').strip()
        if forwarded_host.endswith(':8888') and incoming_api_base in {
            'http://localhost:8000',
            'http://127.0.0.1:8000',
            'http://192.168.0.100:8000',
        }:
            normalized_payload['API_BASE_URL'] = f"http://{forwarded_host}"
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

        await write_db_worker.call_async(lambda session: save_runtime_overrides(session, normalized_payload))

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

        plc_bridge = getattr(request.app.state, 'plc_bridge', None)
        if plc_bridge and any(
            str(key).upper().startswith('PLC_') or str(key).upper() == 'APP_NAME'
            for key in changed_keys
        ):
            await plc_bridge.apply_settings_async(settings)

        yolo_service = getattr(request.app.state, 'yolo', None)
        if (
            yolo_service
            and (settings.yolo_run_mode or 'thread').lower() != 'process'
            and any(str(key).upper().startswith('YOLO_') for key in changed_keys)
        ):
            yolo_service.apply_settings(settings)

        audio_service = getattr(request.app.state, 'audio', None)
        if (
            audio_service
            and (settings.audio_run_mode or 'thread').lower() != 'process'
            and any(str(key).upper().startswith('AUDIO_') for key in changed_keys)
        ):
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
async def get_runtime_config_options() -> Dict[str, object]:
    """返回运行期配置项说明。"""
    settings = get_settings()
    env_entries = load_env_entries(_ENV_PATH)
    sections = _build_runtime_config_sections(settings, env_entries)
    return {'sections': sections}


@router.get('/gui/refresh')
async def gui_refresh(
    request: Request,
    sensors_limit: Optional[int] = Query(default=60, ge=1, le=1000),
    sensors_offset: Optional[int] = Query(default=0, ge=0),
    sensors_start: Optional[str] = Query(default=None),
    sensors_end: Optional[str] = Query(default=None),
    bms_limit: Optional[int] = Query(default=60, ge=1, le=1000),
    bms_offset: Optional[int] = Query(default=0, ge=0),
    bms_start: Optional[str] = Query(default=None),
    bms_end: Optional[str] = Query(default=None),
    rfid_limit: Optional[int] = Query(default=60, ge=1, le=1000),
    rfid_offset: Optional[int] = Query(default=0, ge=0),
    rfid_start: Optional[str] = Query(default=None),
    rfid_end: Optional[str] = Query(default=None),
    alarms_limit: Optional[int] = Query(default=80, ge=1, le=1000),
    alarms_offset: Optional[int] = Query(default=0, ge=0),
    alarms_start: Optional[str] = Query(default=None),
    alarms_end: Optional[str] = Query(default=None),
    alarms_handled: Optional[str] = Query(default=None),
    commands_limit: Optional[int] = Query(default=50, ge=1, le=1000),
    commands_offset: Optional[int] = Query(default=0, ge=0),
    images_limit: Optional[int] = Query(default=30, ge=1, le=1000),
    images_offset: Optional[int] = Query(default=0, ge=0),
    images_start: Optional[str] = Query(default=None),
    images_end: Optional[str] = Query(default=None),
    audio_limit: Optional[int] = Query(default=30, ge=1, le=1000),
    audio_offset: Optional[int] = Query(default=0, ge=0),
    audio_start: Optional[str] = Query(default=None),
    audio_end: Optional[str] = Query(default=None),
    metrics_limit: Optional[int] = Query(default=100, ge=1, le=500),
    data_service: DataService = Depends(get_data_service),
) -> Dict[str, object]:
    """Aggregate GUI refresh payload with per-section timeout and partial failure isolation."""
    start_wall = time.perf_counter()
    read_db_worker = _get_read_db_worker(request)
    alarm_handled_flag = _parse_bool(alarms_handled, 'alarms_handled')

    sensors_start_at = _parse_datetime(sensors_start, 'sensors_start')
    sensors_end_at = _parse_datetime(sensors_end, 'sensors_end')
    sensors_start_at, sensors_end_at, sensors_query_limit = _apply_default_query_window(
        sensors_start_at,
        sensors_end_at,
        _normalize_refresh_limit(sensors_limit, 60),
    )
    sensors_offset_value = int(sensors_offset or 0)

    bms_start_at = _parse_datetime(bms_start, 'bms_start')
    bms_end_at = _parse_datetime(bms_end, 'bms_end')
    bms_start_at, bms_end_at, bms_query_limit = _apply_default_query_window(
        bms_start_at,
        bms_end_at,
        _normalize_refresh_limit(bms_limit, 60),
    )
    bms_offset_value = int(bms_offset or 0)

    rfid_start_at = _parse_datetime(rfid_start, 'rfid_start')
    rfid_end_at = _parse_datetime(rfid_end, 'rfid_end')
    rfid_start_at, rfid_end_at, rfid_query_limit = _apply_default_query_window(
        rfid_start_at,
        rfid_end_at,
        _normalize_refresh_limit(rfid_limit, 60),
    )
    rfid_offset_value = int(rfid_offset or 0)

    commands_query_limit = _normalize_refresh_limit(commands_limit, 50)
    commands_offset_value = int(commands_offset or 0)

    alarms_start_at = _parse_datetime(alarms_start, 'alarms_start')
    alarms_end_at = _parse_datetime(alarms_end, 'alarms_end')
    alarms_query_limit = _normalize_refresh_limit(alarms_limit, 80)
    alarms_offset_value = int(alarms_offset or 0)

    images_start_at = _parse_datetime(images_start, 'images_start')
    images_end_at = _parse_datetime(images_end, 'images_end')
    images_query_limit = _normalize_refresh_limit(images_limit, 30)
    images_offset_value = int(images_offset or 0)

    audio_start_at = _parse_datetime(audio_start, 'audio_start')
    audio_end_at = _parse_datetime(audio_end, 'audio_end')
    audio_query_limit = _normalize_refresh_limit(audio_limit, 30)
    audio_offset_value = int(audio_offset or 0)

    async def _load_db_sections() -> Dict[str, Any]:
        def _query_sections(session):
            service = DataService(session)
            sensors_total = service.count_sensor_data(start=sensors_start_at, end=sensors_end_at)
            sensors_items = service.list_sensor_data(
                start=sensors_start_at,
                end=sensors_end_at,
                limit=sensors_query_limit,
                offset=sensors_offset_value,
            )

            bms_total = service.count_bms_data(start=bms_start_at, end=bms_end_at)
            bms_items = service.list_bms_data(
                start=bms_start_at,
                end=bms_end_at,
                limit=bms_query_limit,
                offset=bms_offset_value,
            )

            rfid_total = service.count_rfid_data(start=rfid_start_at, end=rfid_end_at)
            rfid_items = service.list_rfid_data(
                start=rfid_start_at,
                end=rfid_end_at,
                limit=rfid_query_limit,
                offset=rfid_offset_value,
            )

            commands_total = service.count_command_logs(direction=None)
            command_items = service.list_command_logs(
                direction=None,
                limit=commands_query_limit,
                offset=commands_offset_value,
            )

            config_rows = service.list_sensor_configs()

            alarms_total = service.count_alarm_records(
                start=alarms_start_at,
                end=alarms_end_at,
                handled=alarm_handled_flag,
            )
            alarm_items = service.list_alarm_records(
                start=alarms_start_at,
                end=alarms_end_at,
                handled=alarm_handled_flag,
                limit=alarms_query_limit,
                offset=alarms_offset_value,
            )

            images_total = service.count_image_data(start=images_start_at, end=images_end_at)
            image_rows = service.list_image_data(
                start=images_start_at,
                end=images_end_at,
                limit=images_query_limit,
                offset=images_offset_value,
                include_data=False,
            )

            audio_total = service.count_audio_data(start=audio_start_at, end=audio_end_at)
            audio_rows = service.list_audio_data(
                start=audio_start_at,
                end=audio_end_at,
                limit=audio_query_limit,
                offset=audio_offset_value,
                include_data=False,
            )

            return {
                'sensors': _build_refresh_page(sensors_total, sensors_items),
                'bms': _build_refresh_page(bms_total, bms_items),
                'rfid': _build_refresh_page(rfid_total, rfid_items),
                'commands': _build_refresh_page(commands_total, command_items),
                'configs': _build_refresh_page(len(config_rows), config_rows),
                'alarms': _build_refresh_page(alarms_total, alarm_items),
                'images': _build_refresh_page(
                    images_total,
                    image_rows,
                    include_data=False,
                    row_mapper=lambda row: _image_read_from_row(row, include_data=False).model_dump(),
                ),
                'audio': _build_refresh_page(
                    audio_total,
                    audio_rows,
                    include_data=False,
                    row_mapper=lambda row: _audio_read_from_row(row, include_data=False).model_dump(),
                ),
            }

        return await read_db_worker.call_async(_query_sections)

    async def _load_sensors() -> Dict[str, Any]:
        return (await _load_db_sections())['sensors']

    async def _load_bms() -> Dict[str, Any]:
        return (await _load_db_sections())['bms']

    async def _load_rfid() -> Dict[str, Any]:
        return (await _load_db_sections())['rfid']

    async def _load_commands() -> Dict[str, Any]:
        return (await _load_db_sections())['commands']

    async def _load_configs() -> Dict[str, Any]:
        return (await _load_db_sections())['configs']

    async def _load_alarms() -> Dict[str, Any]:
        return (await _load_db_sections())['alarms']

    async def _load_images() -> Dict[str, Any]:
        return (await _load_db_sections())['images']

    async def _load_audio() -> Dict[str, Any]:
        return (await _load_db_sections())['audio']

    async def _load_audio_metrics() -> Dict[str, Any]:
        limit = min(_normalize_refresh_limit(metrics_limit, 100), 500)
        audio_service = get_audio_service(request)
        metrics = audio_service.get_metrics(limit=limit)
        rows = [row for row in metrics if isinstance(row, dict)]
        return {'total': len(rows), 'items': rows}

    section_keys = (
        'sensors',
        'bms',
        'rfid',
        'commands',
        'configs',
        'alarms',
        'images',
        'audio',
    )

    sections: Dict[str, Dict[str, Any]] = {}
    db_sections_start = time.perf_counter()
    try:
        db_sections_data = await asyncio.wait_for(_load_db_sections(), timeout=8.0)
        db_elapsed_ms = int((time.perf_counter() - db_sections_start) * 1000)
        for key in section_keys:
            sections[key] = {
                'ok': True,
                'timeout': False,
                'error': None,
                'elapsed_ms': db_elapsed_ms,
                'data': db_sections_data.get(key, {'total': 0, 'items': []}),
            }
    except asyncio.TimeoutError:
        db_elapsed_ms = int((time.perf_counter() - db_sections_start) * 1000)
        logger.warning('GUI refresh db batch timeout timeout=8.0s elapsed_ms=%s', db_elapsed_ms)
        for key in section_keys:
            sections[key] = {
                'ok': False,
                'timeout': True,
                'error': f'{key} timeout',
                'elapsed_ms': db_elapsed_ms,
                'data': {'total': 0, 'items': []},
            }
    except HTTPException as exc:
        db_elapsed_ms = int((time.perf_counter() - db_sections_start) * 1000)
        logger.warning('GUI refresh db batch http_error status=%s detail=%s', exc.status_code, exc.detail)
        for key in section_keys:
            sections[key] = {
                'ok': False,
                'timeout': False,
                'error': f'{key} http_{exc.status_code}',
                'elapsed_ms': db_elapsed_ms,
                'data': {'total': 0, 'items': []},
            }
    except Exception as exc:
        db_elapsed_ms = int((time.perf_counter() - db_sections_start) * 1000)
        logger.warning('GUI refresh db batch failed error=%s', exc)
        for key in section_keys:
            sections[key] = {
                'ok': False,
                'timeout': False,
                'error': f'{key} failed: {exc}',
                'elapsed_ms': db_elapsed_ms,
                'data': {'total': 0, 'items': []},
            }

    sections['audio_metrics'] = await _run_refresh_section(
        key='audio_metrics',
        timeout_seconds=5.0,
        loader=_load_audio_metrics,
    )
    elapsed_ms = int((time.perf_counter() - start_wall) * 1000)
    failed = [name for name, payload in sections.items() if not payload.get('ok')]

    logger.info(
        'GUI refresh aggregate done elapsed_ms=%s failed=%s',
        elapsed_ms,
        ','.join(failed) if failed else 'none',
    )
    return {
        'status': 'partial' if failed else 'ok',
        'timestamp': datetime.now().isoformat(timespec='seconds'),
        'elapsed_ms': elapsed_ms,
        'sections': sections,
    }


@router.get('/config/sensors', response_model=List[SensorConfigRead])
async def list_sensor_configs(
    data_service: DataService = Depends(get_data_service),
) -> List[SensorConfigModel]:
    """列出传感器配置。"""
    return await data_service.list_sensor_configs()


@router.post('/config/sensors', response_model=SensorConfigRead, status_code=status.HTTP_201_CREATED)
async def create_sensor_config(
    payload: SensorConfigCreate,
    request: Request,
    data_service: DataService = Depends(get_data_service),
) -> SensorConfigModel:
    """新增或更新传感器配置。"""
    entity = SensorConfigModel(**payload.dict())
    stored = await data_service.upsert_sensor_config(entity)
    await _publish_threshold_alignment(request, data_service)
    return stored


@router.put('/config/sensors/{sensor_type}', response_model=SensorConfigRead)
async def update_sensor_config(
    sensor_type: str,
    payload: SensorConfigCreate,
    request: Request,
    data_service: DataService = Depends(get_data_service),
) -> SensorConfigModel:
    """更新指定传感器配置。"""
    if payload.type != sensor_type:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Payload type does not match path parameter',
        )
    entity = SensorConfigModel(**payload.dict())
    stored = await data_service.upsert_sensor_config(entity)
    await _publish_threshold_alignment(request, data_service)
    return stored


@router.delete('/config/sensors/{sensor_type}', status_code=status.HTTP_204_NO_CONTENT)
async def delete_sensor_config(
    sensor_type: str,
    request: Request,
    data_service: DataService = Depends(get_data_service),
) -> None:
    """删除指定传感器配置。"""
    deleted = await data_service.delete_sensor_config(sensor_type)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Config not found')
    await _publish_threshold_alignment(request, data_service)


async def _publish_threshold_alignment(request: Request, data_service: DataService) -> None:
    mqtt_manager = getattr(request.app.state, 'mqtt', None)
    if mqtt_manager is None:
        logger.warning('Threshold alignment skipped because MQTT manager is unavailable')
        return
    try:
        sensor_configs = await data_service.list_sensor_configs()
        published = await asyncio.to_thread(
            publish_threshold_config_update,
            mqtt_manager,
            sensor_configs,
            version=int(time.time()),
        )
        if not published:
            logger.warning('Threshold alignment publish failed')
    except Exception:
        logger.exception('Threshold alignment publish failed')


@router.get('/alarms', response_model=AlarmRecordPage)
async def list_alarm_records(
    start: Optional[str] = Query(default=None, description='起始时间，ISO8601'),
    end: Optional[str] = Query(default=None, description='结束时间，ISO8601'),
    handled: Optional[str] = Query(default=None, description='是否已处理'),
    limit: Optional[int] = Query(default=None, ge=1, le=1000, description='返回数量上限'),
    offset: int = Query(default=0, ge=0, description='分页偏移量'),
    data_service: DataService = Depends(get_data_service),
) -> AlarmRecordPage:
    """查询报警记录。"""
    start_at = _parse_datetime(start, 'start')
    end_at = _parse_datetime(end, 'end')
    handled_flag = _parse_bool(handled, 'handled')
    total = await data_service.count_alarm_records(
        start=start_at,
        end=end_at,
        handled=handled_flag,
    )
    items = await data_service.list_alarm_records(
        start=start_at,
        end=end_at,
        handled=handled_flag,
        limit=limit,
        offset=offset,
    )
    return AlarmRecordPage(total=total, items=items)


@router.post('/alarms', response_model=AlarmRecordRead, status_code=status.HTTP_201_CREATED)
async def create_alarm_record_api(
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
    stored = await data_service.create_alarm_record(entity)

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
    await publish_alarm_event_async(mqtt_manager, event)
    return stored


@router.get('/images', response_model=ImageDataPage)
async def list_image_data(
    start: Optional[str] = Query(default=None, description='起始时间，ISO8601'),
    end: Optional[str] = Query(default=None, description='结束时间，ISO8601'),
    device_id: Optional[str] = Query(default=None, description='设备编号'),
    location: Optional[str] = Query(default=None, description='安装位置'),
    limit: Optional[int] = Query(default=None, ge=1, le=1000, description='返回数量上限'),
    offset: int = Query(default=0, ge=0, description='分页偏移量'),
    include_data: bool = Query(default=False, description='是否返回图片 base64 数据'),
    timeout_seconds: Optional[float] = Query(default=None, ge=0.1, le=10.0, description='查询超时秒数'),
    settings: Settings = Depends(get_settings),
    data_service: DataService = Depends(get_data_service),
) -> ImageDataPage:
    """查询图像数据。"""
    start_at = _parse_datetime(start, 'start')
    end_at = _parse_datetime(end, 'end')
    effective_timeout = float(
        timeout_seconds
        if timeout_seconds is not None
        else (getattr(settings, 'media_read_query_timeout_seconds', 2.0) or 2.0)
    )
    try:
        total = await asyncio.wait_for(
            data_service.count_image_data(
                start=start_at,
                end=end_at,
                device_id=device_id,
                location=location,
            ),
            timeout=effective_timeout,
        )
        rows = await asyncio.wait_for(
            data_service.list_image_data(
                start=start_at,
                end=end_at,
                device_id=device_id,
                location=location,
                limit=limit,
                offset=offset,
                include_data=include_data,
            ),
            timeout=effective_timeout,
        )
    except asyncio.TimeoutError:
        logger.warning(
            'Image query timeout timeout_seconds=%.2f device_id=%s location=%s',
            effective_timeout,
            device_id or '',
            location or '',
        )
        return ImageDataPage(status='timeout', total=0, items=[])
    items = [_image_read_from_row(row, include_data=include_data) for row in rows]
    return ImageDataPage(status='ok', total=int(total), items=items)


@router.get('/images/{image_id}/data', response_model=ImageDataRead)
async def get_image_data(
    image_id: int,
    data_service: DataService = Depends(get_data_service),
) -> ImageDataRead:
    """按 ID 查询完整图像数据。"""
    row = await data_service.get_image_data(image_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Image record not found')
    return _image_read_from_row(row, include_data=True)


@router.post('/images', response_model=List[ImageDataRead], status_code=status.HTTP_201_CREATED)
async def create_image_data(
    payload: Union[ImageDataCreate, List[ImageDataCreate]],
    request: Request,
    data_service: DataService = Depends(get_data_service),
) -> List[ImageData]:
    """写入图像数据。"""
    media_data_service = _get_media_data_service(request)
    items = payload if isinstance(payload, list) else [payload]
    entities = [ImageData(**item.dict()) for item in items]
    latest_rfid = await data_service.get_latest_rfid_card()
    if latest_rfid:
        for entity in entities:
            entity.location = latest_rfid
    return await media_data_service.create_image_data(entities)


@router.get('/audio', response_model=AudioDataPage)
async def list_audio_data(
    start: Optional[str] = Query(default=None, description='起始时间，ISO8601'),
    end: Optional[str] = Query(default=None, description='结束时间，ISO8601'),
    device_id: Optional[str] = Query(default=None, description='设备编号'),
    location: Optional[str] = Query(default=None, description='安装位置'),
    limit: Optional[int] = Query(default=None, ge=1, le=1000, description='返回数量上限'),
    offset: int = Query(default=0, ge=0, description='分页偏移量'),
    include_data: bool = Query(default=False, description='是否返回音频 base64 数据'),
    timeout_seconds: Optional[float] = Query(default=None, ge=0.1, le=10.0, description='查询超时秒数'),
    settings: Settings = Depends(get_settings),
    data_service: DataService = Depends(get_data_service),
) -> AudioDataPage:
    """查询音频数据。"""
    start_at = _parse_datetime(start, 'start')
    end_at = _parse_datetime(end, 'end')
    effective_timeout = float(
        timeout_seconds
        if timeout_seconds is not None
        else (getattr(settings, 'media_read_query_timeout_seconds', 2.0) or 2.0)
    )
    try:
        total = await asyncio.wait_for(
            data_service.count_audio_data(
                start=start_at,
                end=end_at,
                device_id=device_id,
                location=location,
            ),
            timeout=effective_timeout,
        )
        rows = await asyncio.wait_for(
            data_service.list_audio_data(
                start=start_at,
                end=end_at,
                device_id=device_id,
                location=location,
                limit=limit,
                offset=offset,
                include_data=include_data,
            ),
            timeout=effective_timeout,
        )
    except asyncio.TimeoutError:
        logger.warning(
            'Audio query timeout timeout_seconds=%.2f device_id=%s location=%s',
            effective_timeout,
            device_id or '',
            location or '',
        )
        return AudioDataPage(status='timeout', total=0, items=[])
    items = [_audio_read_from_row(row, include_data=include_data) for row in rows]
    return AudioDataPage(status='ok', total=int(total), items=items)


@router.get('/audio/{audio_id}/data', response_model=AudioDataRead)
async def get_audio_data(
    audio_id: int,
    data_service: DataService = Depends(get_data_service),
) -> AudioDataRead:
    """按 ID 查询完整音频数据。"""
    row = await data_service.get_audio_data(audio_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Audio record not found')
    return _audio_read_from_row(row, include_data=True)


@router.post('/audio', response_model=List[AudioDataRead], status_code=status.HTTP_201_CREATED)
async def create_audio_data(
    payload: Union[AudioDataCreate, List[AudioDataCreate]],
    request: Request,
    data_service: DataService = Depends(get_data_service),
) -> List[AudioDataRead]:
    """写入音频数据。"""
    media_data_service = _get_media_data_service(request)
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
    latest_rfid = await data_service.get_latest_rfid_card()
    if latest_rfid:
        for entity in entities:
            entity.location = latest_rfid
    stored = await media_data_service.create_audio_data(entities)
    return [
        _audio_read_from_row(row, include_data=True)
        for row in stored
    ]


@router.get('/audio/metrics')
async def list_audio_metrics(
    limit: int = Query(default=100, ge=1, le=500, description='返回近期特征数量'),
    audio_service: AudioMonitorService = Depends(get_audio_service),
) -> List[Dict[str, float]]:
    """返回近期计算的音频特征序列。"""
    return audio_service.get_metrics(limit=limit)


@router.post('/audio/thresholds')
async def update_audio_thresholds(
    request: Request,
    background_tasks: BackgroundTasks,
    payload: Dict[str, float],
    audio_service: AudioMonitorService = Depends(get_audio_service),
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
    await _get_write_db_worker(request).call_async(lambda session: (session.add(record), session.commit(), record)[-1])
    settings = get_settings()
    supervisor = getattr(request.app.state, 'supervisor', None)
    if supervisor and (settings.audio_run_mode or 'thread').lower() == 'process':
        background_tasks.add_task(supervisor.restart_audio_worker, reason='audio thresholds updated')
    return data


@router.get('/audio/metrics/stream')
async def stream_audio_metrics(
    audio_service: AudioMonitorService = Depends(get_audio_service),
) -> Dict[str, float]:
    """返回最新一帧音频特征（实时窗口）。"""
    metrics = audio_service.get_metrics(limit=1)
    return metrics[-1] if metrics else {}


@router.get('/metal-anomaly', response_model=MetalAnomalyPage)
async def list_metal_anomaly(
    start: Optional[str] = Query(default=None, description='起始时间，ISO8601'),
    end: Optional[str] = Query(default=None, description='结束时间，ISO8601'),
    device_id: Optional[str] = Query(default=None, description='设备编号'),
    location: Optional[str] = Query(default=None, description='安装位置'),
    limit: Optional[int] = Query(default=None, ge=1, le=1000, description='返回数量上限'),
    offset: int = Query(default=0, ge=0, description='分页偏移量'),
    data_service: DataService = Depends(get_data_service),
) -> MetalAnomalyPage:
    """查询金属异常记录。"""
    start_at = _parse_datetime(start, 'start')
    end_at = _parse_datetime(end, 'end')
    total = await data_service.count_metal_anomaly(
        start=start_at,
        end=end_at,
        device_id=device_id,
        location=location,
    )
    items = await data_service.list_metal_anomaly(
        start=start_at,
        end=end_at,
        device_id=device_id,
        location=location,
        limit=limit,
        offset=offset,
    )
    return MetalAnomalyPage(total=total, items=items)


@router.post('/metal-anomaly', response_model=List[MetalAnomalyRead], status_code=status.HTTP_201_CREATED)
async def create_metal_anomaly(
    payload: Union[MetalAnomalyCreate, List[MetalAnomalyCreate]],
    data_service: DataService = Depends(get_data_service),
) -> List[MetalAnomaly]:
    """写入金属异常记录。"""
    items = payload if isinstance(payload, list) else [payload]
    entities = [MetalAnomaly(**item.dict()) for item in items]
    return await data_service.create_metal_anomaly(entities)
