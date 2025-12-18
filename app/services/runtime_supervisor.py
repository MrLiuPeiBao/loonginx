"""Runtime supervision for startup dependencies.

This module is responsible for keeping critical background components alive:
- Database initialization (MySQL might not be ready at process start).
- MQTT connection loop (broker might not be ready at process start).
- Background services (YOLO / Audio) should only start after DB init succeeds.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional

from app.db.session import db_ping, init_db
from app.mqtt import MQTTManager
from app.services.audio_service import AudioMonitorService
from app.services.yolo_service import YOLOStreamService

logger = logging.getLogger(__name__)


@dataclass
class RuntimeStatus:
    """Snapshot for readiness/liveness reporting."""

    db_ok: bool
    mqtt_ok: bool
    db_initialized: bool
    services_started: bool
    last_db_error: Optional[str] = None
    last_db_init_attempt_at: Optional[float] = None


class RuntimeSupervisor:
    """Continuously ensure DB/MQTT and start background services when ready."""

    def __init__(
        self,
        *,
        mqtt_manager: MQTTManager,
        yolo_service: YOLOStreamService,
        audio_service: AudioMonitorService,
        poll_seconds: float = 2.0,
        db_retry_max_seconds: float = 30.0,
    ) -> None:
        """Create a supervisor instance.

        Args:
            mqtt_manager (MQTTManager): Shared MQTT manager.
            yolo_service (YOLOStreamService): YOLO background service.
            audio_service (AudioMonitorService): Audio background service.
            poll_seconds (float, optional): Supervisor polling interval.
            db_retry_max_seconds (float, optional): Max backoff for DB init retry.
        """
        self._mqtt = mqtt_manager
        self._yolo = yolo_service
        self._audio = audio_service
        self._poll_seconds = max(0.5, float(poll_seconds))
        self._db_retry_max_seconds = max(2.0, float(db_retry_max_seconds))

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._status = RuntimeStatus(
            db_ok=False,
            mqtt_ok=False,
            db_initialized=False,
            services_started=False,
        )

    def start(self) -> None:
        """Start supervisor thread (idempotent)."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name='runtime-supervisor',
            daemon=True,
        )
        self._thread.start()
        logger.info('Runtime supervisor started')

    def stop(self, timeout: float = 3.0) -> None:
        """Stop supervisor thread."""
        self._stop_event.set()
        thread = self._thread
        self._thread = None
        if thread and thread.is_alive():
            thread.join(timeout=timeout)
        logger.info('Runtime supervisor stopped')

    def get_status(self) -> Dict[str, object]:
        """Get a thread-safe status snapshot."""
        with self._lock:
            status = self._status
            return {
                'db_ok': status.db_ok,
                'mqtt_ok': status.mqtt_ok,
                'db_initialized': status.db_initialized,
                'services_started': status.services_started,
                'last_db_error': status.last_db_error,
                'last_db_init_attempt_at': status.last_db_init_attempt_at,
            }

    def is_ready(self) -> bool:
        """Return readiness state for watchdog."""
        with self._lock:
            return bool(self._status.db_ok and self._status.mqtt_ok)

    # Internal ---------------------------------------------------------
    def _run(self) -> None:
        db_backoff = 1.0
        while not self._stop_event.is_set():
            self._ensure_mqtt()
            self._ensure_db_initialized(backoff_seconds=db_backoff)

            status_db_ok = db_ping()
            status_mqtt_ok = self._mqtt.is_connected

            with self._lock:
                self._status.db_ok = bool(status_db_ok)
                self._status.mqtt_ok = bool(status_mqtt_ok)

            if self._maybe_start_services():
                db_backoff = 1.0
            else:
                db_backoff = min(self._db_retry_max_seconds, max(1.0, db_backoff * 2.0))

            self._wait(self._poll_seconds)

    def _ensure_mqtt(self) -> None:
        """Ensure MQTT loop is running and broker reconnection is attempted."""
        try:
            self._mqtt.connect()
        except Exception:  # pragma: no cover - defensive
            logger.exception('MQTT ensure failed')

    def _ensure_db_initialized(self, *, backoff_seconds: float) -> None:
        """Ensure init_db() eventually succeeds (retry with backoff)."""
        with self._lock:
            initialized = bool(self._status.db_initialized)
        if initialized:
            return

        attempt_at = time.time()
        with self._lock:
            self._status.last_db_init_attempt_at = attempt_at

        try:
            init_db()
        except Exception as exc:  # pragma: no cover - depends on external DB
            message = str(exc)
            with self._lock:
                self._status.last_db_error = message
            logger.warning('Database not ready, retrying in %.1fs: %s', backoff_seconds, message)
            self._wait(min(backoff_seconds, self._db_retry_max_seconds))
            return

        with self._lock:
            self._status.db_initialized = True
            self._status.last_db_error = None
        logger.info('Database initialized successfully')

    def _maybe_start_services(self) -> bool:
        """Start YOLO/Audio after DB init succeeds (idempotent)."""
        with self._lock:
            if not self._status.db_initialized:
                return False
            if self._status.services_started:
                return True

        try:
            self._yolo.start()
        except Exception:  # pragma: no cover - defensive
            logger.exception('Failed to start YOLO service')
        try:
            self._audio.start()
        except Exception:  # pragma: no cover - defensive
            logger.exception('Failed to start Audio service')

        with self._lock:
            self._status.services_started = True
        logger.info('Background services started')
        return True

    def _wait(self, seconds: float) -> None:
        end = time.time() + seconds
        while not self._stop_event.is_set() and time.time() < end:
            time.sleep(0.2)

