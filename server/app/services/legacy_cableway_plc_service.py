from __future__ import annotations

import json
import queue
import threading
import time
import warnings
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, TYPE_CHECKING

from app.core.config import Settings
from app.core.constants import MQTT_TOPICS
from app.db.session import db_ping
from app.mqtt import MQTTMessageContext
from app.services.cableway_cache import (
    get_latest_cableway_status as get_cached_cableway_status,
    set_latest_cableway_status_snapshot,
)
from app.services.cableway_plc import (
    CablewayCommandExecution,
    CablewayPLC,
    CablewayPLCConfig,
    CablewayPLCYieldToUrgentWork,
    _now_iso,
    _now_ts,
)
from app.services.plc_logging import get_plc_logger

if TYPE_CHECKING:
    from app.mqtt import MQTTManager


logger = get_plc_logger(__name__)

_PRIORITY_CONTROL = 0
_PRIORITY_INTERACTIVE_READ = 5
_PRIORITY_BACKGROUND_POLL = 10


@dataclass
class _PLCCommandTicket:
    done: threading.Event = field(default_factory=threading.Event)
    execution: Optional[CablewayCommandExecution] = None


@dataclass(order=True)
class _PLCWorkItem:
    priority: int
    sequence: int
    kind: str = field(compare=False)
    command_type: Optional[str] = field(default=None, compare=False)
    request_id: Optional[str] = field(default=None, compare=False)
    device_id: Optional[str] = field(default=None, compare=False)
    command_code: Optional[int] = field(default=None, compare=False)
    pulse: bool = field(default=True, compare=False)
    urgent: bool = field(default=False, compare=False)
    ticket: Optional[_PLCCommandTicket] = field(default=None, compare=False)


class LegacyCablewayPLCService:
    """Legacy compatibility shim for the retired in-process PLC runtime."""

    def __init__(self, settings: Settings, mqtt_manager: Optional["MQTTManager"] = None):
        warnings.warn(
            "CablewayPLCService is legacy; use app.plc_rt for production PLC control.",
            DeprecationWarning,
            stacklevel=2,
        )
        self._settings = settings
        self._mqtt_manager = mqtt_manager
        self._plc: Optional[CablewayPLC] = None
        self._lock = threading.RLock()
        self._state_lock = threading.Lock()
        self._queue: queue.PriorityQueue[_PLCWorkItem] = queue.PriorityQueue()
        self._worker_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._enabled = False
        self._sequence = 0
        self._urgent_waiting = 0
        self._poll_enqueued = False
        self._next_poll_due_at = 0.0
        self._build_from_settings(settings)

    @property
    def enabled(self) -> bool:
        return bool(self._enabled and self._plc is not None)

    def start(self) -> None:
        if not self.enabled:
            return
        if self._worker_thread and self._worker_thread.is_alive():
            return
        self._stop_event.clear()
        with self._state_lock:
            self._next_poll_due_at = time.monotonic()
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            name="legacy-server-cableway-plc",
            daemon=True,
        )
        self._worker_thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop_event.set()
        try:
            self._queue.put_nowait(
                _PLCWorkItem(priority=-1, sequence=self._next_sequence(), kind="stop"),
            )
        except Exception:
            pass

        thread = self._worker_thread
        self._worker_thread = None
        if thread and thread.is_alive():
            thread.join(timeout=timeout)

        with self._lock:
            plc = self._plc
            self._plc = None
        if plc:
            plc.close()

        with self._state_lock:
            self._queue = queue.PriorityQueue()
            self._urgent_waiting = 0
            self._poll_enqueued = False
            self._next_poll_due_at = 0.0

    def close(self) -> None:
        self.stop()

    def apply_settings(self, settings: Settings) -> None:
        was_running = bool(self._worker_thread and self._worker_thread.is_alive())
        self.stop()
        self._settings = settings
        self._build_from_settings(settings)
        if was_running or self.enabled:
            self.start()

    def execute_command(
        self,
        *,
        command_type: str,
        request_id: Optional[str] = None,
        device_id: Optional[str] = None,
        params: Optional[Dict[str, float]] = None,
        command_code: Optional[int] = None,
        pulse: bool = True,
    ) -> CablewayCommandExecution:
        del params

        response: Dict[str, Any] = {
            "timestamp": _now_iso(),
            "ts": _now_ts(),
            "schema": 1,
            "payload_type": "cableway_command_response",
            "device_id": self._resolve_device_id(device_id),
            "type": str(command_type or "").strip() or "unknown",
            "success": False,
        }
        if request_id:
            response["request_id"] = request_id

        try:
            self._require_plc()
            if not (self._worker_thread and self._worker_thread.is_alive()):
                self.start()
            priority, urgent = self._resolve_command_priority(command_type)
            ticket = _PLCCommandTicket()
            item = _PLCWorkItem(
                priority=priority,
                sequence=self._next_sequence(),
                kind="command",
                command_type=str(command_type or "").strip(),
                request_id=request_id,
                device_id=device_id,
                command_code=command_code,
                pulse=bool(pulse),
                urgent=urgent,
                ticket=ticket,
            )
            if urgent:
                self._mark_urgent_waiting(1)
            enqueued = False
            try:
                self._queue.put(item)
                enqueued = True
            finally:
                if urgent and not enqueued:
                    self._mark_urgent_waiting(-1)

            timeout_seconds = max(5.0, float(self._settings.command_timeout_seconds or 15))
            if not ticket.done.wait(timeout=timeout_seconds):
                raise TimeoutError(
                    f"Timed out waiting for direct PLC command execution after {timeout_seconds:.1f}s"
                )
            if ticket.execution is None:
                raise RuntimeError("Direct PLC command completed without a result")
            return ticket.execution
        except Exception as exc:
            response["error"] = str(exc)
            logger.exception(
                "Server direct PLC command failed type=%s request_id=%s error=%s",
                command_type,
                request_id,
                exc,
            )
        return CablewayCommandExecution(response_payload=response)

    def _build_from_settings(self, settings: Settings) -> None:
        with self._lock:
            self._enabled = bool(settings.plc_direct_enabled)
            self._plc = None
            if not self._enabled:
                return
            config = CablewayPLCConfig(
                host=str(settings.plc_host),
                port=int(settings.plc_port),
                unit_id=int(settings.plc_unit_id),
                timeout=float(settings.plc_timeout),
                connect_timeout=float(settings.plc_connect_timeout),
                status_poll_interval=float(settings.plc_status_poll_interval),
                heartbeat_interval=float(settings.plc_heartbeat_interval),
                command_pulse_seconds=float(settings.plc_command_pulse_seconds),
                even_byte_is_high=bool(settings.plc_even_byte_is_high),
                float_word_order=str(settings.plc_float_word_order or "big").strip().lower(),
                float_byte_order=str(settings.plc_float_byte_order or "big").strip().lower(),
            )
            self._plc = CablewayPLC(config)

    def _poll_interval(self) -> float:
        return max(0.0, float(self._settings.plc_status_poll_interval or 0.0))

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            self._maybe_schedule_poll()
            try:
                item = self._queue.get(timeout=0.05)
            except queue.Empty:
                continue

            if item.kind == "stop":
                self._queue.task_done()
                break

            if item.urgent:
                self._mark_urgent_waiting(-1)

            try:
                if item.kind == "poll":
                    self._execute_poll_item()
                elif item.kind == "command" and item.ticket is not None:
                    item.ticket.execution = self._execute_command_item(item)
                    item.ticket.done.set()
            finally:
                self._queue.task_done()

    def _execute_poll_item(self) -> None:
        self._mark_poll_consumed()
        try:
            plc = self._require_plc()
            status = plc.poll_status_and_heartbeat(should_yield=self._should_yield_to_urgent_work)
        except CablewayPLCYieldToUrgentWork:
            self._request_poll(delay=0.0)
            return
        except Exception:
            logger.exception("Server direct PLC polling loop failed")
            self._request_poll(delay=self._poll_interval())
            return

        if status and self._mqtt_manager is not None and db_ping():
            logger.warning("Legacy CablewayPLCService poll result ignored; plc_rt owns the active PLC runtime path")
        self._request_poll(delay=self._poll_interval())

    def _execute_command_item(self, item: _PLCWorkItem) -> CablewayCommandExecution:
        response: Dict[str, Any] = {
            "timestamp": _now_iso(),
            "ts": _now_ts(),
            "schema": 1,
            "payload_type": "cableway_command_response",
            "device_id": self._resolve_device_id(item.device_id),
            "type": str(item.command_type or "").strip() or "unknown",
            "success": False,
        }
        if item.request_id:
            response["request_id"] = item.request_id

        status_payload: Optional[Dict[str, Any]] = None
        frames: list[Dict[str, Any]] = []
        plc = self._require_plc()
        plc.set_trace_sink(lambda entry: frames.append(dict(entry)))
        try:
            result: Optional[Dict[str, Any]] = None
            if item.command_type == "control":
                if item.command_code is None:
                    raise ValueError("command_code is required for control")
                plc.send_control_command(int(item.command_code), pulse=bool(item.pulse))
                result = self._get_cached_status_result(device_id=item.device_id)
                self._request_poll(delay=0.0)
            elif item.command_type == "estop":
                plc.send_estop(pulse=bool(item.pulse))
                result = self._get_cached_status_result(device_id=item.device_id)
                self._request_poll(delay=0.0)
            elif item.command_type == "read_status":
                result = plc.read_status()
            elif item.command_type == "query_faults":
                result = plc.query_faults()
            else:
                raise ValueError(f"unsupported command type={item.command_type}")

            response["success"] = True
            response["plc_host"] = plc.host
            if result is not None:
                response["result"] = result
                status_payload = self._build_status_payload(
                    result,
                    device_id=item.device_id,
                )
        except Exception as exc:
            response["error"] = str(exc)
            logger.exception(
                "Server direct PLC command failed type=%s request_id=%s error=%s",
                item.command_type,
                item.request_id,
                exc,
            )
        finally:
            plc.set_trace_sink(None)

        return CablewayCommandExecution(
            response_payload=response,
            status_payload=status_payload,
            frames=tuple(frames),
        )

    def _build_status_context(self, status: Dict[str, Any], *, device_id: Optional[str] = None) -> MQTTMessageContext:
        payload = self._build_status_payload(status, device_id=device_id)
        return MQTTMessageContext(
            topic=MQTT_TOPICS["cableway_status"],
            payload=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            qos=0,
            retain=False,
        )

    def _build_status_payload(self, status: Dict[str, Any], *, device_id: Optional[str] = None) -> Dict[str, Any]:
        timestamp = status.get("timestamp") or _now_iso()
        ts = status.get("ts") or _now_ts()
        device = self._resolve_device_id(device_id)
        location = self._resolve_location()
        payload = {
            "payload_type": "cableway_status",
            "device_id": device,
            "location": location,
            "timestamp": timestamp,
            "ts": ts,
            "schema": 1,
            "plc_host": status.get("plc_host") or self._settings.plc_host,
            "status": dict(status),
        }
        set_latest_cableway_status_snapshot(payload)
        return payload

    def _get_cached_status_result(self, *, device_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        cached = get_cached_cableway_status()
        if not isinstance(cached, dict):
            return None
        target_device = str(device_id or "").strip()
        cached_device = str(cached.get("device_id") or "").strip()
        if target_device and cached_device and cached_device != target_device:
            return None
        status = cached.get("status")
        if not isinstance(status, dict):
            return None
        return dict(status)

    def _resolve_device_id(self, device_id: Optional[str] = None) -> str:
        preferred = str(device_id or "").strip()
        if preferred:
            return preferred
        configured = str(self._settings.plc_device_id or "").strip()
        if configured:
            return configured
        return str(self._settings.app_name or "server-plc").strip() or "server-plc"

    def _resolve_location(self) -> str:
        return str(self._settings.plc_location or "").strip() or "unknown"

    def _require_plc(self) -> CablewayPLC:
        with self._lock:
            if not self._enabled or self._plc is None:
                raise RuntimeError("server direct PLC mode is not enabled")
            return self._plc

    def _resolve_command_priority(self, command_type: str) -> tuple[int, bool]:
        normalized = str(command_type or "").strip()
        if normalized in {"control", "estop"}:
            return _PRIORITY_CONTROL, True
        return _PRIORITY_INTERACTIVE_READ, False

    def _next_sequence(self) -> int:
        with self._state_lock:
            self._sequence += 1
            return self._sequence

    def _mark_urgent_waiting(self, delta: int) -> None:
        with self._state_lock:
            self._urgent_waiting = max(0, self._urgent_waiting + int(delta))

    def _should_yield_to_urgent_work(self) -> bool:
        with self._state_lock:
            return self._urgent_waiting > 0

    def _maybe_schedule_poll(self) -> None:
        if not self.enabled:
            return
        poll_interval = self._poll_interval()
        if poll_interval <= 0:
            return
        with self._state_lock:
            if self._poll_enqueued or time.monotonic() < self._next_poll_due_at:
                return
            self._poll_enqueued = True
        self._queue.put(
            _PLCWorkItem(
                priority=_PRIORITY_BACKGROUND_POLL,
                sequence=self._next_sequence(),
                kind="poll",
            )
        )

    def _request_poll(self, *, delay: float) -> None:
        with self._state_lock:
            self._next_poll_due_at = time.monotonic() + max(0.0, float(delay))

    def _mark_poll_consumed(self) -> None:
        with self._state_lock:
            self._poll_enqueued = False


CablewayPLCService = LegacyCablewayPLCService
