"""FastAPI application factory."""

from __future__ import annotations

import logging
import json
from datetime import datetime

from fastapi import FastAPI

from app.api.routes import router as api_router
from app.core.config import get_settings, load_env
from app.core.constants import MQTT_TOPICS
from app.db.models import CommandDirection
from app.db.session import session_scope
from app.mqtt import MQTTManager, MQTTMessageContext, MQTTMessageHandler
from app.services import DataService, YOLOStreamService
from app.services.audio_service import AudioMonitorService
from app.services.runtime_supervisor import RuntimeSupervisor
from app.services.ingestion import (
    handle_bms_payload,
    handle_rfid_payload,
    handle_sensor_payload,
)

logger = logging.getLogger(__name__)


def _bytes_to_hex(data: bytes) -> str:
    """Convert bytes into space separated hex string."""
    return ' '.join(f'{byte:02X}' for byte in data)


def create_app() -> FastAPI:
    """Create and configure FastAPI application."""
    load_env()
    settings = get_settings()
    application = FastAPI(
        title=settings.app_name,
        version='0.1.0',
    )

    mqtt_manager = MQTTManager(settings)
    application.state.mqtt = mqtt_manager
    yolo_service = YOLOStreamService(settings, mqtt_manager)
    application.state.yolo = yolo_service
    audio_service = AudioMonitorService(settings, mqtt_manager=mqtt_manager)
    application.state.audio = audio_service
    supervisor = RuntimeSupervisor(
        mqtt_manager=mqtt_manager,
        yolo_service=yolo_service,
        audio_service=audio_service,
    )
    application.state.supervisor = supervisor

    def command_response_handler(context: MQTTMessageContext) -> None:
        """Persist command response messages to database."""
        payload_hex = _bytes_to_hex(context.payload)
        payload_text = None
        payload_json = None
        try:
            payload_text = context.payload.decode('utf-8')
            payload_json = json.loads(payload_text)
        except Exception:
            payload_text = None

        payload_for_log = payload_hex
        notes = [f'MQTT:{context.topic}']
        device_id = None

        if isinstance(payload_json, dict):
            request_hex = payload_json.get('request_hex') or payload_json.get('command')
            response_hex = payload_json.get('response_hex') or payload_json.get('response')
            success = payload_json.get('success')
            device_id = payload_json.get('device_id')
            request_id = payload_json.get('request_id')
            payload_for_log = response_hex or payload_text or payload_hex
            if request_id:
                notes.append(f'request_id={request_id}')
            if success is not None:
                notes.append(f'success={success}')
            if request_hex:
                notes.append(f'request={request_hex}')
        elif payload_text:
            payload_for_log = payload_text

        try:
            with session_scope() as session:
                data_service = DataService(session)
                data_service.add_command_log(
                    timestamp=datetime.utcnow(),
                    direction=CommandDirection.RESPONSE,
                    payload=str(payload_for_log),
                    notes=' | '.join(notes),
                    device_id=device_id,
                )
        except Exception:  # pragma: no cover - external services
            logger.exception('Failed to persist MQTT command response')

    mqtt_manager.register_handler(
        MQTT_TOPICS['command_response'],
        command_response_handler,
    )

    def sensor_payload_handler(context: MQTTMessageContext) -> None:
        handle_sensor_payload(context, mqtt_manager=mqtt_manager)

    def bms_payload_handler(context: MQTTMessageContext) -> None:
        handle_bms_payload(context, mqtt_manager=mqtt_manager)

    mqtt_manager.register_handler(MQTT_TOPICS['sensor_data'], sensor_payload_handler)
    mqtt_manager.register_handler(MQTT_TOPICS['bms_data'], bms_payload_handler)
    mqtt_manager.register_handler(MQTT_TOPICS['rfid_data'], handle_rfid_payload)

    @application.on_event('startup')
    def on_startup() -> None:
        supervisor.start()

    @application.on_event('shutdown')
    def on_shutdown() -> None:
        supervisor.stop()
        yolo_service.stop()
        audio_service.stop()
        mqtt_manager.disconnect()

    application.include_router(api_router)
    return application
