"""FastAPI application factory."""

from __future__ import annotations

import logging
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    from app.api.routes import router as api_router
    from app.core.config import get_settings, load_env
    from app.core.constants import MQTT_TOPICS
    from app.mqtt import MQTTMessageContext
    from app.runtime import DBWorker, MQTTWorker, PLCBridge, PLCEventBridgeServer, TelemetryBridgeServer, MediaWriteWorker
    from app.services.audio_service import AudioMonitorService
    from app.services.command_ingestion import handle_command_response_payload
    from app.services.cableway_ingestion import handle_cableway_status_payload
    from app.services.config_sync import handle_config_ack, handle_device_hello
    from app.services.ingestion import (
        handle_bms_payload,
        handle_rfid_payload,
        handle_sensor_payload,
    )
    from app.services.runtime_supervisor import RuntimeSupervisor
    from app.services.yolo_service import YOLOStreamService

    load_env()
    settings = get_settings()
    application = FastAPI(
        title=settings.app_name,
        version="0.1.0",
    )
    application.state.settings = settings
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.effective_cors_allow_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
        allow_headers=["*"],
        expose_headers=["*"],
        max_age=600,
    )

    read_db_worker = DBWorker(name='db-read-worker')
    write_db_worker = DBWorker(name='db-write-worker')
    application.state.read_db_worker = read_db_worker
    application.state.write_db_worker = write_db_worker
    # Keep legacy alias to avoid breaking older call-sites/tests during migration.
    application.state.db_worker = write_db_worker
    media_db_worker = DBWorker(name='db-media-worker')
    application.state.media_db_worker = media_db_worker
    mqtt_manager = MQTTWorker(settings)
    application.state.mqtt = mqtt_manager
    media_write_worker = MediaWriteWorker(
        db_worker=media_db_worker,
        mqtt_worker=mqtt_manager,
        max_queue_size=max(1, int(getattr(settings, 'media_write_queue_capacity', 1000) or 1000)),
    )
    application.state.media_write_worker = media_write_worker
    plc_bridge = PLCBridge(settings)
    application.state.plc_bridge = plc_bridge
    plc_event_bridge = PLCEventBridgeServer(settings=settings, db_worker=write_db_worker, mqtt_worker=mqtt_manager)
    application.state.plc_event_bridge = plc_event_bridge
    telemetry_bridge = TelemetryBridgeServer(settings=settings, media_write_worker=media_write_worker)
    application.state.telemetry_bridge = telemetry_bridge
    yolo_service = YOLOStreamService(settings, mqtt_manager, db_worker=write_db_worker)
    application.state.yolo = yolo_service
    audio_service = AudioMonitorService(settings, mqtt_manager=mqtt_manager, db_worker=write_db_worker)
    application.state.audio = audio_service
    supervisor = RuntimeSupervisor(
        mqtt_manager=mqtt_manager,
        yolo_service=yolo_service,
        audio_service=audio_service,
        read_db_worker=read_db_worker,
        write_db_worker=write_db_worker,
        settings=settings,
    )
    application.state.supervisor = supervisor

    @application.middleware("http")
    async def _slow_request_logging(request: Request, call_next):
        started_at = time.perf_counter()
        response = None
        try:
            response = await call_next(request)
        finally:
            elapsed_ms = (time.perf_counter() - started_at) * 1000.0
            if elapsed_ms >= 1000.0:
                try:
                    read_queue_size = int(read_db_worker.queue_size())
                except Exception:
                    read_queue_size = -1
                try:
                    write_queue_size = int(write_db_worker.queue_size())
                except Exception:
                    write_queue_size = -1
                status_code = getattr(response, "status_code", 500)
                logger.warning(
                    "Slow API request method=%s path=%s status=%s elapsed_ms=%.1f read_db_q=%s write_db_q=%s",
                    request.method,
                    request.url.path,
                    status_code,
                    elapsed_ms,
                    read_queue_size,
                    write_queue_size,
                )
        return response

    mqtt_manager.register_handler(
        MQTT_TOPICS["command_response"],
        lambda context: handle_command_response_payload(context, db_worker=write_db_worker),
    )
    mqtt_manager.register_handler(
        MQTT_TOPICS["config_ack"],
        lambda context: handle_config_ack(context, db_worker=write_db_worker),
    )
    mqtt_manager.register_handler(
        MQTT_TOPICS["device_hello"],
        lambda context: handle_device_hello(context, mqtt_manager, db_worker=write_db_worker),
    )

    def sensor_payload_handler(context: MQTTMessageContext) -> None:
        handle_sensor_payload(context, mqtt_manager=mqtt_manager, db_worker=write_db_worker)

    def bms_payload_handler(context: MQTTMessageContext) -> None:
        handle_bms_payload(context, mqtt_manager=mqtt_manager, db_worker=write_db_worker)

    mqtt_manager.register_handler(MQTT_TOPICS["sensor_data"], sensor_payload_handler)
    mqtt_manager.register_handler(MQTT_TOPICS["bms_data"], bms_payload_handler)
    mqtt_manager.register_handler(MQTT_TOPICS["rfid_data"], lambda context: handle_rfid_payload(context, db_worker=write_db_worker))
    mqtt_manager.register_handler(
        MQTT_TOPICS["cableway_status"],
        lambda context: handle_cableway_status_payload(context, mqtt_manager=mqtt_manager, db_worker=write_db_worker),
    )

    @application.on_event("startup")
    def on_startup() -> None:
        read_db_worker.start()
        write_db_worker.start()
        media_db_worker.start()
        if settings.plc_direct_enabled:
            plc_event_bridge.start()
        else:
            logger.info("PLC event bridge disabled because PLC_DIRECT_ENABLED=false")
        media_write_worker.start()
        telemetry_bridge.start()
        supervisor.start()

    @application.on_event("shutdown")
    def on_shutdown() -> None:
        supervisor.stop()
        yolo_service.stop()
        audio_service.stop()
        mqtt_manager.disconnect()
        plc_event_bridge.stop()
        telemetry_bridge.stop()
        media_write_worker.stop()
        media_db_worker.stop()
        write_db_worker.stop()
        read_db_worker.stop()

    application.include_router(api_router)
    return application
