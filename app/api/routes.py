"""REST API routes."""

from __future__ import annotations

import base64
import logging
from datetime import datetime
from typing import Dict, List, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from sqlmodel import Session

from app.core.config import Settings, get_settings
from app.core.constants import MQTT_TOPICS
from app.db.models import (
    AlarmRecord,
    AudioData,
    BMSData,
    CommandDirection,
    CommandLog,
    ImageData,
    MetalAnomaly,
    RFIDData,
    SensorConfig as SensorConfigModel,
    SensorData,
)
from app.db.session import db_ping, get_session
from app.schemas.alarm import AlarmRecordCreate, AlarmRecordRead
from app.schemas.audio import AudioDataCreate, AudioDataRead
from app.db.audio_thresholds import AudioThreshold
from app.schemas.bms import BMSDataCreate, BMSDataRead
from app.schemas.command import CommandLogRead, CommandRequest
from app.schemas.config import SensorConfigCreate, SensorConfigRead
from app.schemas.image import ImageDataCreate, ImageDataRead
from app.schemas.metal import MetalAnomalyCreate, MetalAnomalyRead
from app.schemas.rfid import RFIDDataCreate, RFIDDataRead
from app.schemas.sensor import SensorDataCreate, SensorDataRead
from app.services import DataService, AudioMonitorService
from app.services.alarm_publisher import build_alarm_event, publish_alarm_event
from app.services.bms_alerts import maybe_create_bms_low_voltage_alarm


router = APIRouter(prefix='/api', tags=['api'])
logger = logging.getLogger(__name__)


def get_data_service(session: Session = Depends(get_session)) -> DataService:
    """Return a DataService instance."""
    return DataService(session)

def get_audio_service(request: Request) -> AudioMonitorService:
    """Return audio monitor service from app state if available."""
    service = getattr(request.app.state, 'audio', None)
    if service is None:
        raise HTTPException(status_code=503, detail='Audio service not available')
    return service


def _is_probable_wav(data: bytes) -> bool:
    """Heuristic check for WAV container header."""
    return len(data) >= 12 and data[:4] in (b'RIFF', b'RIFX') and data[8:12] == b'WAVE'


def _audio_db_value_to_base64(value: object) -> str:
    """Convert DB audio payload (bytes or legacy text) into base64 string for JSON."""
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
        'timestamp': datetime.utcnow().isoformat(timespec='seconds'),
        **status_payload,
    }


@router.get('/health/live')
def health_live(settings: Settings = Depends(get_settings)) -> dict:
    """Liveness probe: process is running."""
    return {
        'status': 'ok',
        'app': settings.app_name,
        'timestamp': datetime.utcnow().isoformat(timespec='seconds'),
    }


@router.get('/health/ready')
def health_ready(request: Request, settings: Settings = Depends(get_settings)) -> JSONResponse:
    """Readiness probe: DB + MQTT must be available."""
    mqtt_manager = getattr(request.app.state, 'mqtt', None)
    mqtt_ok = bool(getattr(mqtt_manager, 'is_connected', False))
    payload: Dict[str, object] = {
        'status': 'ok',
        'app': settings.app_name,
        'timestamp': datetime.utcnow().isoformat(timespec='seconds'),
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
    start_at = _parse_datetime(start, 'start')
    end_at = _parse_datetime(end, 'end')
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
    start_at = _parse_datetime(start, 'start')
    end_at = _parse_datetime(end, 'end')
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
    start_at = _parse_datetime(start, 'start')
    end_at = _parse_datetime(end, 'end')
    return data_service.list_rfid_data(
        start=start_at,
        end=end_at,
        device_id=device_id,
        limit=limit,
        offset=offset,
    )


@router.post('/rfid', response_model=List[RFIDDataRead], status_code=status.HTTP_201_CREATED)
def create_rfid_data(
    payload: Union[RFIDDataCreate, List[RFIDDataCreate]],
    data_service: DataService = Depends(get_data_service),
) -> List[RFIDData]:
    """插入 RFID 数据。"""
    items = payload if isinstance(payload, list) else [payload]
    entities = [RFIDData(**item.dict()) for item in items]
    return data_service.create_rfid_data(entities)


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
        timestamp=datetime.utcnow(),
        direction=CommandDirection.REQUEST,
        payload=payload_text,
        notes=notes or None,
        device_id=request.device_id,
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
    timestamp = payload.timestamp or datetime.utcnow()
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
) -> List[ImageData]:
    """查询图像数据。"""
    start_at = _parse_datetime(start, 'start')
    end_at = _parse_datetime(end, 'end')
    return data_service.list_image_data(
        start=start_at,
        end=end_at,
        device_id=device_id,
        location=location,
        limit=limit,
        offset=offset,
    )


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
            audio_data=_audio_db_value_to_base64(row.audio_data),
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
            audio_data=_audio_db_value_to_base64(row.audio_data),
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
