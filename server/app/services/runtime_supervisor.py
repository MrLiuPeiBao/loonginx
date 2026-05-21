"""Runtime supervision for startup dependencies.

This module is responsible for keeping critical background components alive:
- Database initialization (MySQL might not be ready at process start).
- MQTT connection loop (broker might not be ready at process start).
- Background services (YOLO / Audio) should only start after DB init succeeds.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import subprocess
import sys
import shlex
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, Optional

from app.db.session import db_ping, init_db
from app.db.models import CommandStatus
from app.core.config import Settings
from app.runtime.db_worker import DBWorker
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
    media_gateway_process_alive: bool = False
    last_db_error: Optional[str] = None
    last_db_init_attempt_at: Optional[float] = None


class RuntimeSupervisor:
    """Continuously ensure DB/MQTT and start background services when ready."""

    def __init__(
        self,
        *,
        mqtt_manager,
        yolo_service: YOLOStreamService,
        audio_service: AudioMonitorService,
        db_worker: Optional[DBWorker] = None,
        read_db_worker: Optional[DBWorker] = None,
        write_db_worker: Optional[DBWorker] = None,
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
        self._read_db_worker = read_db_worker or db_worker or write_db_worker
        self._write_db_worker = write_db_worker or db_worker or read_db_worker
        if self._read_db_worker is None or self._write_db_worker is None:
            raise ValueError('RuntimeSupervisor requires db workers')
        self._settings = settings
        self._poll_seconds = max(0.5, float(poll_seconds))
        self._db_retry_max_seconds = max(2.0, float(db_retry_max_seconds))
        self._command_timeout_seconds = max(0, int(settings.command_timeout_seconds or 0))
        self._command_timeout_check_interval = max(2.0, self._poll_seconds * 5.0)
        self._last_command_timeout_check = 0.0
        self._yolo_process: Optional[subprocess.Popen] = None
        self._audio_process: Optional[subprocess.Popen] = None
        self._media_gateway_process: Optional[subprocess.Popen] = None
        self._plc_rt_process: Optional[subprocess.Popen] = None
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
            read_queue_size = getattr(self._read_db_worker, 'queue_size', None)
            write_queue_size = getattr(self._write_db_worker, 'queue_size', None)
            return {
                'db_ok': status.db_ok,
                'mqtt_ok': status.mqtt_ok,
                'db_initialized': status.db_initialized,
                'services_started': status.services_started,
                'read_db_worker_queue_size': int(read_queue_size() if callable(read_queue_size) else 0),
                'write_db_worker_queue_size': int(write_queue_size() if callable(write_queue_size) else 0),
                'db_worker_queue_size': int(write_queue_size() if callable(write_queue_size) else 0),
                'yolo_process_alive': status.yolo_process_alive,
                'audio_process_alive': status.audio_process_alive,
                'media_gateway_process_alive': status.media_gateway_process_alive,
                'media_gateway_enabled': bool(self._settings.is_media_gateway_enabled()),
                'media_gateway_type': str(getattr(self._settings, 'media_gateway_type', '') or ''),
                'media_gateway_exec': str(getattr(self._settings, 'media_gateway_exec', '') or ''),
                'media_gateway_relay_rtsp': str(getattr(self._settings, 'media_gateway_relay_rtsp', '') or ''),
                'plc_rt_process_alive': bool(self._plc_rt_process and self._plc_rt_process.poll() is None),
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
            self._start_media_gateway_service()
            self._start_plc_rt_service()
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
                self._status.media_gateway_process_alive = bool(
                    self._media_gateway_process and self._media_gateway_process.poll() is None
                )

            if self._ensure_background_services():
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
                self._write_db_worker.call_data_service(
                    'prune_old_records',
                    days=int(self._settings.data_retention_days or 0),
                    tables=tables,
                )
                self._write_db_worker.call_data_service(
                    'prune_by_size',
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

    def _ensure_background_services(self) -> bool:
        """Start or keep YOLO/Audio alive after DB init succeeds."""
        with self._lock:
            if not self._status.db_initialized:
                return False

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
        logger.debug('Background services ensured')
        return True

    def _start_plc_rt_service(self) -> None:
        if not self._settings.plc_direct_enabled:
            self._stop_plc_rt_worker()
            return
        self._plc_rt_process = self._ensure_worker_process(
            name='plc-rt',
            module='app.plc_rt.process',
            existing=self._plc_rt_process,
        )

    def _start_media_gateway_service(self) -> None:
        if not self._settings.is_media_gateway_enabled():
            self._stop_media_gateway_worker()
            return
        mode = (self._settings.media_gateway_run_mode or 'process').lower()
        if mode != 'process':
            logger.warning(
                'MEDIA_GATEWAY_RUN_MODE=%s is unsupported, fallback to process',
                mode,
            )
        self._media_gateway_process = self._ensure_media_gateway_process(
            existing=self._media_gateway_process,
        )
        with self._lock:
            self._status.media_gateway_process_alive = bool(
                self._media_gateway_process and self._media_gateway_process.poll() is None
            )
        self._stop_conflicting_rtsp_output_bindings()

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

    def restart_yolo_worker(self, *, reason: str = '') -> None:
        """Restart YOLO worker process (process mode only)."""
        mode = (self._settings.yolo_run_mode or 'thread').lower()
        if mode != 'process':
            self._stop_yolo_worker()
            return
        if not self._settings.yolo_enabled:
            self._stop_yolo_worker()
            return
        logger.info('Restarting yolo-worker (%s)', reason or 'settings updated')
        self._stop_yolo_worker()
        self._yolo_process = self._ensure_worker_process(
            name='yolo-worker',
            module='app.services.yolo_worker',
            existing=None,
        )
        with self._lock:
            self._status.yolo_process_alive = bool(self._yolo_process and self._yolo_process.poll() is None)

    def restart_media_gateway_worker(self, *, reason: str = '') -> None:
        """Restart media gateway worker process."""
        if not self._settings.is_media_gateway_enabled():
            self._stop_media_gateway_worker()
            return
        logger.info('Restarting media-gateway worker (%s)', reason or 'settings updated')
        self._stop_media_gateway_worker()
        self._media_gateway_process = self._ensure_media_gateway_process(existing=None)
        with self._lock:
            self._status.media_gateway_process_alive = bool(
                self._media_gateway_process and self._media_gateway_process.poll() is None
            )

    def _stop_yolo_worker(self) -> None:
        proc = self._yolo_process
        if proc and proc.poll() is None:
            logger.info('Stopping yolo-worker process')
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
        self._yolo_process = None
        with self._lock:
            self._status.yolo_process_alive = False

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

    def _stop_media_gateway_worker(self) -> None:
        proc = self._media_gateway_process
        if proc and proc.poll() is None:
            logger.info('Stopping media-gateway process')
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
        self._media_gateway_process = None
        with self._lock:
            self._status.media_gateway_process_alive = False

    def _ensure_worker_process(
        self,
        *,
        name: str,
        module: str,
        existing: Optional[subprocess.Popen],
    ) -> Optional[subprocess.Popen]:
        if existing and existing.poll() is None:
            return existing
        self._stop_stale_worker_processes(module=module)
        python_bin = sys.executable or 'python'
        logger.info('Starting %s process: %s -m %s', name, python_bin, module)
        try:
            proc = subprocess.Popen([python_bin, '-m', module], cwd=Path(__file__).resolve().parents[2])
            return proc
        except Exception as exc:
            logger.exception('Failed to start %s process: %s', name, exc)
            return None

    def _build_media_gateway_command(self) -> list[str]:
        exec_path = str(self._settings.media_gateway_exec or '').strip()
        if not exec_path:
            return []
        args_text = str(self._settings.media_gateway_args or '').strip()
        try:
            args = shlex.split(args_text, posix=False) if args_text else []
        except Exception:
            logger.warning('Invalid MEDIA_GATEWAY_ARGS, use raw text fallback: %s', args_text)
            args = [args_text] if args_text else []
        return [exec_path, *args]

    def _parse_rtsp_port(self, url: str) -> Optional[int]:
        from urllib.parse import urlparse

        text = str(url or '').strip()
        if not text:
            return None
        try:
            parsed = urlparse(text)
        except Exception:
            return None
        try:
            return int(parsed.port or 0) or None
        except Exception:
            return None

    def _stop_conflicting_rtsp_output_bindings(self) -> None:
        gateway_port = self._parse_rtsp_port(getattr(self._settings, 'media_gateway_relay_rtsp', ''))
        output_port = self._parse_rtsp_port(getattr(self._settings, 'yolo_rtsp_output', ''))
        if gateway_port is None or output_port is None or gateway_port != output_port:
            return
        worker = self._yolo_process
        if worker and worker.poll() is None:
            logger.warning(
                'Detected YOLO RTSP output port conflict with media gateway: %s; restarting yolo-worker',
                gateway_port,
            )
            self.restart_yolo_worker(reason='rtsp port conflict with media gateway')

    def _resolve_media_gateway_cwd(self) -> Path:
        default_cwd = Path(__file__).resolve().parents[2]
        workdir_text = str(self._settings.media_gateway_workdir or '').strip()
        if not workdir_text:
            return default_cwd
        try:
            candidate = Path(workdir_text).expanduser()
            if not candidate.is_absolute():
                candidate = (default_cwd / candidate).resolve()
            if candidate.exists() and candidate.is_dir():
                return candidate
        except Exception:
            pass
        logger.warning(
            'MEDIA_GATEWAY_WORKDIR is invalid, fallback to default: %s',
            workdir_text,
        )
        return default_cwd

    def _ensure_media_gateway_process(
        self,
        *,
        existing: Optional[subprocess.Popen],
    ) -> Optional[subprocess.Popen]:
        if existing and existing.poll() is None:
            return existing
        command = self._build_media_gateway_command()
        if not command:
            logger.warning(
                'MEDIA_GATEWAY_ENABLED=true but MEDIA_GATEWAY_EXEC is empty; skip media gateway start',
            )
            return None
        exec_name = Path(command[0]).name or command[0]
        self._stop_stale_external_processes(exec_name=exec_name, command_signature=' '.join(command))
        cwd = self._resolve_media_gateway_cwd()
        logger.info('Starting media-gateway process: %s (cwd=%s)', ' '.join(command), cwd)
        try:
            return subprocess.Popen(command, cwd=cwd)
        except Exception as exc:
            logger.exception('Failed to start media-gateway process: %s', exc)
            return None

    def _handle_runtime_changes(self, changed_keys: list[str]) -> None:
        media_gateway_changed = any(str(key).upper().startswith('MEDIA_GATEWAY_') for key in changed_keys)
        if any(str(key).upper().startswith('PLC_') for key in changed_keys):
            self._restart_plc_rt_worker(reason='PLC_* updated')
        if any(str(key).upper().startswith('YOLO_') for key in changed_keys):
            self.restart_yolo_worker(reason='YOLO_* updated')
        if any(str(key).upper().startswith('AUDIO_') for key in changed_keys):
            self.restart_audio_worker(reason='AUDIO_* updated')
        if media_gateway_changed:
            self.restart_media_gateway_worker(reason='MEDIA_GATEWAY_* updated')
            # Effective RTSP inputs may change when gateway rewrite toggles.
            self.restart_yolo_worker(reason='MEDIA_GATEWAY_* updated')
            self.restart_audio_worker(reason='MEDIA_GATEWAY_* updated')

    def _restart_plc_rt_worker(self, *, reason: str = '') -> None:
        if not self._settings.plc_direct_enabled:
            self._stop_plc_rt_worker()
            return
        logger.info('Restarting plc-rt worker (%s)', reason or 'settings updated')
        self._stop_plc_rt_worker()
        self._plc_rt_process = self._ensure_worker_process(
            name='plc-rt',
            module='app.plc_rt.process',
            existing=None,
        )

    def _stop_plc_rt_worker(self) -> None:
        proc = self._plc_rt_process
        if proc and proc.poll() is None:
            logger.info('Stopping plc-rt process')
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
        self._plc_rt_process = None

    def _stop_worker_processes(self) -> None:
        for proc, name in (
            (self._media_gateway_process, 'media-gateway'),
            (self._plc_rt_process, 'plc-rt'),
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
            (self._media_gateway_process, 'media-gateway'),
            (self._plc_rt_process, 'plc-rt'),
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
        self._media_gateway_process = None
        self._plc_rt_process = None
        self._yolo_process = None
        self._audio_process = None

    def cleanup_before_exec_restart(self) -> None:
        """Best-effort cleanup before API process replaces itself via os.execv()."""
        self._stop_event.set()
        self._stop_worker_processes()
        self._stop_stale_worker_processes(module='app.services.yolo_worker')
        self._stop_stale_worker_processes(module='app.services.audio_worker')
        media_cmd = self._build_media_gateway_command()
        if media_cmd:
            exec_name = Path(media_cmd[0]).name or media_cmd[0]
            self._stop_stale_external_processes(
                exec_name=exec_name,
                command_signature=' '.join(media_cmd),
            )

    def _stop_stale_worker_processes(self, *, module: str) -> None:
        current_pid = os.getpid()
        try:
            candidates = (
                subprocess.check_output(
                    [
                        "powershell",
                        "-NoProfile",
                        "-Command",
                        (
                            "Get-CimInstance Win32_Process -Filter \"Name = 'python.exe'\" "
                            f"| Where-Object {{ $_.CommandLine -like '*{module}*' }} "
                            "| Select-Object ProcessId,ParentProcessId | ConvertTo-Json -Compress"
                        ),
                    ],
                    text=True,
                    stderr=subprocess.DEVNULL,
                ).strip()
            )
        except Exception:
            return
        if not candidates:
            return
        try:
            import json

            rows = json.loads(candidates)
        except Exception:
            return
        if isinstance(rows, dict):
            rows = [rows]
        for row in rows or []:
            try:
                pid = int(row.get('ProcessId') or 0)
                parent_pid = int(row.get('ParentProcessId') or 0)
            except Exception:
                continue
            if pid <= 0 or pid == current_pid or parent_pid == current_pid:
                continue
            try:
                logger.warning('Stopping stale worker pid=%s module=%s parent_pid=%s', pid, module, parent_pid)
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            except Exception:
                logger.debug('Failed to stop stale worker pid=%s', pid, exc_info=True)

    def _stop_stale_external_processes(self, *, exec_name: str, command_signature: str) -> None:
        current_pid = os.getpid()
        normalized_name = str(exec_name or '').strip()
        if not normalized_name:
            return
        signature = str(command_signature or '').strip()
        signature_lower = signature.lower()
        escaped_name = normalized_name.replace("'", "''")
        try:
            candidates = (
                subprocess.check_output(
                    [
                        "powershell",
                        "-NoProfile",
                        "-Command",
                        (
                            "Get-CimInstance Win32_Process "
                            f"| Where-Object {{ $_.Name -eq '{escaped_name}' }} "
                            "| Select-Object ProcessId,ParentProcessId,CommandLine "
                            "| ConvertTo-Json -Compress"
                        ),
                    ],
                    text=True,
                    stderr=subprocess.DEVNULL,
                ).strip()
            )
        except Exception:
            return
        if not candidates:
            return
        try:
            import json

            rows = json.loads(candidates)
        except Exception:
            return
        if isinstance(rows, dict):
            rows = [rows]
        for row in rows or []:
            try:
                pid = int(row.get('ProcessId') or 0)
                parent_pid = int(row.get('ParentProcessId') or 0)
            except Exception:
                continue
            if pid <= 0 or pid == current_pid or parent_pid == current_pid:
                continue
            cmdline = str(row.get('CommandLine') or '')
            if signature_lower and signature_lower not in cmdline.lower():
                continue
            try:
                logger.warning(
                    'Stopping stale external process pid=%s exec=%s signature=%s',
                    pid,
                    normalized_name,
                    signature,
                )
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            except Exception:
                logger.debug('Failed to stop stale external process pid=%s', pid, exc_info=True)

    def _ensure_command_timeouts(self) -> None:
        if self._command_timeout_seconds <= 0:
            return
        now = time.time()
        if now - self._last_command_timeout_check < self._command_timeout_check_interval:
            return
        self._last_command_timeout_check = now
        try:
            timed_out = self._write_db_worker.call_data_service(
                'mark_command_timeouts',
                timeout_seconds=self._command_timeout_seconds,
            )
            if timed_out:
                logger.info('Command timeout marked count=%s', timed_out)
        except Exception:  # pragma: no cover - DB dependency
            logger.debug('Command timeout check failed', exc_info=True)

    def _wait(self, seconds: float) -> None:
        end = time.time() + seconds
        while not self._stop_event.is_set() and time.time() < end:
            time.sleep(0.2)
