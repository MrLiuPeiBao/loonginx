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
import subprocess
import sys
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, Optional

from app.db.session import db_ping, init_db, session_scope
from app.services.data_service import DataService
from app.db.models import CommandStatus
from app.core.config import Settings
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
    yolo_process_alive: bool = False
    audio_process_alive: bool = False
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
        settings: Settings,
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
        self._settings = settings
        self._poll_seconds = max(0.5, float(poll_seconds))
        self._db_retry_max_seconds = max(2.0, float(db_retry_max_seconds))
        self._command_timeout_seconds = max(0, int(settings.command_timeout_seconds or 0))
        self._command_timeout_check_interval = max(2.0, self._poll_seconds * 5.0)
        self._last_command_timeout_check = 0.0
        self._yolo_process: Optional[subprocess.Popen] = None
        self._audio_process: Optional[subprocess.Popen] = None
        self._retention_thread: Optional[threading.Thread] = None
        self._retention_enabled = bool(
            (settings.data_retention_days or 0) > 0
            or (settings.data_retention_max_gb or 0) > 0
        )
        self._retention_interval = max(
            60,
            int(settings.data_retention_check_interval_seconds or 3600),
        )

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._status = RuntimeStatus(
            db_ok=False,
            mqtt_ok=False,
            db_initialized=False,
            services_started=False,
        )

    def apply_settings(self, settings: Settings, *, changed_keys: Optional[list[str]] = None) -> None:
        """Apply new settings to supervisor runtime."""
        self._settings = settings
        self._command_timeout_seconds = max(0, int(settings.command_timeout_seconds or 0))
        self._command_timeout_check_interval = max(2.0, self._poll_seconds * 5.0)
        self._retention_enabled = bool(
            (settings.data_retention_days or 0) > 0
            or (settings.data_retention_max_gb or 0) > 0
        )
        self._retention_interval = max(
            60,
            int(settings.data_retention_check_interval_seconds or 3600),
        )
        if self._retention_enabled:
            self._start_retention_worker()
        if changed_keys:
            self._handle_runtime_changes(changed_keys)

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
        self._start_retention_worker()
        logger.info('Runtime supervisor started')

    def stop(self, timeout: float = 3.0) -> None:
        """Stop supervisor thread."""
        self._stop_event.set()
        thread = self._thread
        self._thread = None
        if thread and thread.is_alive():
            thread.join(timeout=timeout)
        retention_thread = self._retention_thread
        self._retention_thread = None
        if retention_thread and retention_thread.is_alive():
            retention_thread.join(timeout=timeout)
        self._stop_worker_processes()
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
                'yolo_process_alive': status.yolo_process_alive,
                'audio_process_alive': status.audio_process_alive,
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
            self._ensure_command_timeouts()

            status_db_ok = db_ping()
            status_mqtt_ok = self._mqtt.is_connected

            with self._lock:
                self._status.db_ok = bool(status_db_ok)
                self._status.mqtt_ok = bool(status_mqtt_ok)
                self._status.yolo_process_alive = bool(
                    self._yolo_process and self._yolo_process.poll() is None
                )
                self._status.audio_process_alive = bool(
                    self._audio_process and self._audio_process.poll() is None
                )

            if self._maybe_start_services():
                db_backoff = 1.0
            else:
                db_backoff = min(self._db_retry_max_seconds, max(1.0, db_backoff * 2.0))

            self._wait(self._poll_seconds)

    def _start_retention_worker(self) -> None:
        if not self._retention_enabled:
            return
        if self._retention_thread and self._retention_thread.is_alive():
            return
        self._retention_thread = threading.Thread(
            target=self._run_retention_loop,
            name='retention-worker',
            daemon=True,
        )
        self._retention_thread.start()

    def _run_retention_loop(self) -> None:
        while not self._stop_event.is_set():
            if not self._retention_enabled:
                self._wait(self._retention_interval)
                continue
            try:
                tables = self._settings.data_retention_tables
                with session_scope() as session:
                    service = DataService(session)
                    service.prune_old_records(
                        days=int(self._settings.data_retention_days or 0),
                        tables=tables,
                    )
                    service.prune_by_size(
                        max_gb=float(self._settings.data_retention_max_gb or 0),
                        tables=tables,
                    )
            except Exception:
                logger.exception('Retention cleanup failed')
            self._wait(self._retention_interval)

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
            self._start_yolo_service()
        except Exception:  # pragma: no cover - defensive
            logger.exception('Failed to start YOLO service')
        try:
            self._start_audio_service()
        except Exception:  # pragma: no cover - defensive
            logger.exception('Failed to start Audio service')

        with self._lock:
            self._status.services_started = True
        logger.info('Background services started')
        return True

    def _start_yolo_service(self) -> None:
        if not self._settings.yolo_enabled:
            return
        mode = (self._settings.yolo_run_mode or 'thread').lower()
        if mode == 'process':
            self._yolo_process = self._ensure_worker_process(
                name='yolo-worker',
                module='app.services.yolo_worker',
                existing=self._yolo_process,
            )
            with self._lock:
                self._status.yolo_process_alive = bool(self._yolo_process and self._yolo_process.poll() is None)
            return
        self._yolo.start()

    def _start_audio_service(self) -> None:
        if not self._settings.audio_enabled:
            return
        mode = (self._settings.audio_run_mode or 'thread').lower()
        if mode == 'process':
            self._audio_process = self._ensure_worker_process(
                name='audio-worker',
                module='app.services.audio_worker',
                existing=self._audio_process,
            )
            with self._lock:
                self._status.audio_process_alive = bool(self._audio_process and self._audio_process.poll() is None)
            return
        self._audio.start()

    def restart_audio_worker(self, *, reason: str = '') -> None:
        """Restart audio worker process (process mode only)."""
        mode = (self._settings.audio_run_mode or 'thread').lower()
        if mode != 'process':
            self._stop_audio_worker()
            return
        if not self._settings.audio_enabled:
            self._stop_audio_worker()
            return
        logger.info('Restarting audio worker (%s)', reason or 'settings updated')
        self._stop_audio_worker()
        self._audio_process = self._ensure_worker_process(
            name='audio-worker',
            module='app.services.audio_worker',
            existing=None,
        )
        with self._lock:
            self._status.audio_process_alive = bool(self._audio_process and self._audio_process.poll() is None)

    def _stop_audio_worker(self) -> None:
        proc = self._audio_process
        if proc and proc.poll() is None:
            logger.info('Stopping audio-worker process')
            try:
                proc.terminate()
            except Exception:
                pass
        if proc and proc.poll() is None:
            try:
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        self._audio_process = None
        with self._lock:
            self._status.audio_process_alive = False

    def _ensure_worker_process(
        self,
        *,
        name: str,
        module: str,
        existing: Optional[subprocess.Popen],
    ) -> Optional[subprocess.Popen]:
        if existing and existing.poll() is None:
            return existing
        python_bin = sys.executable or 'python'
        logger.info('Starting %s process: %s -m %s', name, python_bin, module)
        try:
            proc = subprocess.Popen([python_bin, '-m', module], cwd=Path(__file__).resolve().parents[2])
            return proc
        except Exception as exc:
            logger.exception('Failed to start %s process: %s', name, exc)
            return None

    def _handle_runtime_changes(self, changed_keys: list[str]) -> None:
        if any(str(key).upper().startswith('AUDIO_') for key in changed_keys):
            self.restart_audio_worker(reason='AUDIO_* updated')

    def _stop_worker_processes(self) -> None:
        for proc, name in (
            (self._yolo_process, 'yolo-worker'),
            (self._audio_process, 'audio-worker'),
        ):
            if proc and proc.poll() is None:
                logger.info('Stopping %s process', name)
                try:
                    proc.terminate()
                except Exception:
                    pass
        for proc, name in (
            (self._yolo_process, 'yolo-worker'),
            (self._audio_process, 'audio-worker'),
        ):
            if proc and proc.poll() is None:
                try:
                    proc.wait(timeout=3)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
        self._yolo_process = None
        self._audio_process = None

    def _ensure_command_timeouts(self) -> None:
        if self._command_timeout_seconds <= 0:
            return
        now = time.time()
        if now - self._last_command_timeout_check < self._command_timeout_check_interval:
            return
        self._last_command_timeout_check = now
        try:
            with session_scope() as session:
                service = DataService(session)
                timed_out = service.mark_command_timeouts(timeout_seconds=self._command_timeout_seconds)
                if timed_out:
                    logger.info('Command timeout marked count=%s', timed_out)
        except Exception:  # pragma: no cover - DB dependency
            logger.debug('Command timeout check failed', exc_info=True)

    def _wait(self, seconds: float) -> None:
        end = time.time() + seconds
        while not self._stop_event.is_set() and time.time() < end:
            time.sleep(0.2)
