from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from app.plc_rt.events import build_fault_transition, build_status_snapshot
from app.plc_rt.poll_plan import POLL_STEP_4, build_default_poll_steps
from app.plc_rt.scheduler import PLCJob, PLCScheduler
from app.plc_rt.state_store import PLCStateStore
from app.services.cableway_plc import (
    CablewayCommandExecution,
    CablewayPLC,
    CablewayPLCConfig,
    CablewayPLCYieldToUrgentWork,
)
from app.services.cableway_specs import TOTAL_FAULT_KEY
from app.services.modbus_tcp import ModbusTCPError
from app.services.plc_logging import get_plc_logger
from app.services.plc_timing import emit_plc_timing, new_trace_id


logger = get_plc_logger(__name__)


@dataclass
class _PartialStatus:
    motion: Dict[str, float]
    current_task_code: Optional[int]
    status_bits: Dict[str, bool]
    faults: Dict[str, bool]
    fault_query_performed: bool


class PLCIOWorker:
    def __init__(
        self,
        *,
        config: CablewayPLCConfig,
        scheduler: PLCScheduler,
        state_store: PLCStateStore,
        emit_event: Callable[[Dict[str, Any]], None],
    ) -> None:
        self._plc = CablewayPLC(config)
        self._scheduler = scheduler
        self._state_store = state_store
        self._emit_event = emit_event
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._next_heartbeat_due = time.monotonic()
        self._next_poll_due = time.monotonic()
        self._partial = _PartialStatus(motion={}, current_task_code=None, status_bits={}, faults={}, fault_query_performed=False)
        self._poll_steps = build_default_poll_steps()
        self._last_io_error_key: Optional[tuple[str, str]] = None
        self._last_io_error_logged_at = 0.0
        self._io_error_suppressed = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="plc-io-thread", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop_event.set()
        self._scheduler.submit_internal(priority=-1, kind="stop", body={})
        thread = self._thread
        self._thread = None
        if thread and thread.is_alive():
            thread.join(timeout=timeout)
        self._plc.close()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            now = time.monotonic()
            if now >= self._next_heartbeat_due:
                heartbeat_trace_id = new_trace_id("heartbeat")
                emit_plc_timing(
                    "svc.poll_scheduled",
                    request_id="",
                    trace_id=heartbeat_trace_id,
                    job_kind="heartbeat",
                    priority=20,
                    queue_depth=self._scheduler.qsize(),
                )
                self._scheduler.submit_internal(priority=20, kind="heartbeat", body={"trace_id": heartbeat_trace_id, "job_kind": "heartbeat"})
                self._next_heartbeat_due = now + float(self._plc.heartbeat_interval or 1.0)
            if now >= self._next_poll_due:
                poll_trace_id = new_trace_id("poll")
                emit_plc_timing(
                    "svc.poll_scheduled",
                    request_id="",
                    trace_id=poll_trace_id,
                    job_kind="poll_step",
                    priority=30,
                    queue_depth=self._scheduler.qsize(),
                )
                self._scheduler.schedule_poll_step(
                    step_name=self._poll_steps[0].name,
                    body={"trace_id": poll_trace_id, "job_kind": "poll_step"},
                )
                self._next_poll_due = now + float(self._plc.status_poll_interval or 0.5)

            try:
                job = self._scheduler.get(timeout=0.1)
            except queue.Empty:
                continue

            try:
                if job.kind == "stop":
                    return
                if job.kind.startswith("poll_step_"):
                    emit_plc_timing(
                        "svc.poll_dequeue",
                        request_id=job.request_id,
                        trace_id=str(job.body.get("trace_id") or ""),
                        job_kind=str(job.body.get("job_kind") or "poll_step"),
                        priority=job.priority,
                        queue_depth=self._scheduler.qsize(),
                        queue_wait_ns=max(0, time.perf_counter_ns() - int(job.enqueued_mono_ns)),
                        urgent_waiting=self._scheduler.urgent_pending(),
                    )
                else:
                    emit_plc_timing(
                        "svc.dequeue",
                        request_id=job.request_id,
                        trace_id=str(job.body.get("trace_id") or ""),
                        job_kind=str(job.body.get("job_kind") or job.kind),
                        priority=job.priority,
                        queue_depth=self._scheduler.qsize(),
                        queue_wait_ns=max(0, time.perf_counter_ns() - int(job.enqueued_mono_ns)),
                        urgent_waiting=self._scheduler.urgent_pending(),
                        command_type=job.body.get("type"),
                        command_code=job.body.get("command_code"),
                        device_id=job.body.get("device_id"),
                    )
                self._set_plc_trace_context(self._job_trace_context(job))
                if job.kind == "heartbeat":
                    self._plc._maybe_send_heartbeat()
                elif job.kind.startswith("poll_step_"):
                    self._execute_poll_step(job)
                else:
                    self._execute_command(job)
            except ModbusTCPError as exc:
                self._log_io_error(job=job, exc=exc, include_traceback=False)
                if job.ticket is not None:
                    job.ticket.response = {
                        "response_payload": {
                            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            "payload_type": "cableway_command_response",
                            "request_id": job.request_id,
                            "type": job.body.get("type"),
                            "device_id": job.body.get("device_id") or "server-plc",
                            "success": False,
                            "error": str(exc),
                        },
                        "status_payload": None,
                        "frames": [],
                    }
                    job.ticket.done.set()
            except Exception as exc:
                self._log_io_error(job=job, exc=exc, include_traceback=True)
                if job.ticket is not None:
                    job.ticket.response = {
                        "response_payload": {
                            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            "payload_type": "cableway_command_response",
                            "request_id": job.request_id,
                            "type": job.body.get("type"),
                            "device_id": job.body.get("device_id") or "server-plc",
                            "success": False,
                            "error": str(exc),
                        },
                        "status_payload": None,
                        "frames": [],
                    }
                    job.ticket.done.set()
            finally:
                self._set_plc_trace_context(None)
                if job.kind.startswith("poll_step_"):
                    self._scheduler.mark_poll_consumed()
                self._scheduler.task_done()

    def _execute_command(self, job: PLCJob) -> None:
        emit_plc_timing(
            "svc.command_start",
            request_id=job.request_id,
            trace_id=str(job.body.get("trace_id") or ""),
            job_kind="command",
            priority=job.priority,
            command_type=job.body.get("type"),
            command_code=job.body.get("command_code"),
            device_id=job.body.get("device_id"),
        )
        response_payload: Dict[str, Any] = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "payload_type": "cableway_command_response",
            "request_id": job.request_id,
            "type": job.body.get("type"),
            "device_id": job.body.get("device_id") or "server-plc",
            "success": False,
        }
        status_payload: Optional[Dict[str, Any]] = None
        frames: list[Dict[str, Any]] = []
        self._plc.set_trace_sink(lambda entry: frames.append(dict(entry)))
        try:
            command_type = str(job.body.get("type") or "")
            if command_type == "control":
                self._plc.send_control_command(int(job.body["command_code"]), pulse=bool(job.body.get("pulse", True)))
                response_payload["result"] = "frame_sent"
            elif command_type == "estop":
                self._plc.send_estop(pulse=bool(job.body.get("pulse", True)))
                response_payload["result"] = "frame_sent"
            elif command_type == "read_status":
                result = self._plc.read_status()
                response_payload["result"] = result
                status_payload = {"payload_type": "cableway_status", "status": result, "device_id": response_payload["device_id"], "location": "server", "timestamp": result.get("timestamp"), "ts": result.get("ts"), "schema": 1, "plc_host": result.get("plc_host")}
            elif command_type == "query_faults":
                result = self._plc.query_faults()
                response_payload["result"] = result
                status_payload = {"payload_type": "cableway_status", "status": result, "device_id": response_payload["device_id"], "location": "server", "timestamp": result.get("timestamp"), "ts": result.get("ts"), "schema": 1, "plc_host": result.get("plc_host")}
            else:
                raise ValueError(f"unsupported command type={command_type}")
            response_payload["success"] = True
            response_payload["frames"] = list(frames)
        except Exception as exc:
            response_payload["error"] = str(exc)
        finally:
            self._plc.set_trace_sink(None)

        if job.ticket is not None:
            job.ticket.response = {
                "response_payload": response_payload,
                "status_payload": status_payload,
                "frames": list(frames),
            }
            job.ticket.done.set()

        if status_payload:
            self._publish_snapshot(status_payload["status"], reason="command_followup")
        emit_plc_timing(
            "svc.command_end",
            request_id=job.request_id,
            trace_id=str(job.body.get("trace_id") or ""),
            job_kind="command",
            priority=job.priority,
            command_type=job.body.get("type"),
            command_code=job.body.get("command_code"),
            device_id=job.body.get("device_id"),
            success=bool(response_payload.get("success")),
            error=response_payload.get("error"),
        )

    def _execute_poll_step(self, job: PLCJob) -> None:
        step_name = str(job.kind)
        trace_id = str(job.body.get("trace_id") or "")
        poll_fields = {
            "request_id": job.request_id,
            "trace_id": trace_id,
            "job_kind": str(job.body.get("job_kind") or "poll_step"),
            "priority": job.priority,
            "queue_depth": self._scheduler.qsize(),
            "urgent_waiting": self._scheduler.urgent_pending(),
        }
        emit_plc_timing("svc.poll_start", stage_step=step_name, **poll_fields)
        try:
            if step_name == "poll_step_1":
                self._partial.motion = self._plc.read_motion_values(should_yield=self._scheduler.urgent_pending)
                self._scheduler.continue_poll_step(step_name="poll_step_2", body=dict(job.body))
                emit_plc_timing("svc.poll_rescheduled", stage_step="poll_step_2", **poll_fields)
            elif step_name == "poll_step_2":
                self._partial.current_task_code = self._plc.read_current_task_code(should_yield=self._scheduler.urgent_pending)
                self._scheduler.continue_poll_step(step_name="poll_step_3", body=dict(job.body))
                emit_plc_timing("svc.poll_rescheduled", stage_step="poll_step_3", **poll_fields)
            elif step_name == "poll_step_3":
                self._partial.status_bits = self._plc.read_status_bits(should_yield=self._scheduler.urgent_pending)
                self._partial.faults = {TOTAL_FAULT_KEY: bool(self._partial.status_bits.get(TOTAL_FAULT_KEY))}
                self._partial.fault_query_performed = False
                if self._partial.faults[TOTAL_FAULT_KEY]:
                    self._scheduler.continue_poll_step(step_name=POLL_STEP_4.name, body=dict(job.body))
                    emit_plc_timing("svc.poll_rescheduled", stage_step=POLL_STEP_4.name, **poll_fields)
                else:
                    self._publish_snapshot(self._build_snapshot(), reason="poll_step")
                    self._partial = _PartialStatus(motion={}, current_task_code=None, status_bits={}, faults={}, fault_query_performed=False)
            elif step_name == "poll_step_4":
                self._partial.fault_query_performed = True
                self._partial.faults = self._plc.read_fault_details(should_yield=self._scheduler.urgent_pending)
                self._partial.faults[TOTAL_FAULT_KEY] = True
                self._publish_snapshot(self._build_snapshot(), reason="fault_query")
                self._partial = _PartialStatus(motion={}, current_task_code=None, status_bits={}, faults={}, fault_query_performed=False)
        except CablewayPLCYieldToUrgentWork:
            emit_plc_timing("svc.poll_yield_to_urgent", stage_step=step_name, **poll_fields)
            self._scheduler.continue_poll_step(step_name=step_name, body=dict(job.body))
            emit_plc_timing("svc.poll_rescheduled", stage_step=step_name, **poll_fields)
        finally:
            emit_plc_timing("svc.poll_end", stage_step=step_name, **poll_fields)

    def _build_snapshot(self) -> Dict[str, Any]:
        outputs = {
            "zt_home_done": bool(self._partial.status_bits.get("zt_home_done")),
            "zt_position_done": bool(self._partial.status_bits.get("zt_position_done")),
        }
        return {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "ts": time.time(),
            "plc_host": self._plc.host,
            **self._partial.motion,
            "current_task_code": int(self._partial.current_task_code or 0),
            "total_fault": bool(self._partial.faults.get(TOTAL_FAULT_KEY)),
            "home_completed": outputs["zt_home_done"],
            "positioning_completed": outputs["zt_position_done"],
            "faults": dict(self._partial.faults),
            "outputs": outputs,
            "active_faults": [key for key, enabled in self._partial.faults.items() if enabled],
            "fault_query_performed": bool(self._partial.fault_query_performed),
            "fault_query_error": None,
        }

    def _publish_snapshot(self, snapshot: Dict[str, Any], *, reason: str) -> None:
        update = self._state_store.update(snapshot)
        self._emit_event(build_status_snapshot(seq=update.seq, reason=reason, snapshot=update.snapshot))
        if update.raised or update.cleared:
            self._emit_event(build_fault_transition(seq=update.seq, raised=update.raised, cleared=update.cleared))

    def _job_trace_context(self, job: PLCJob) -> Dict[str, Any]:
        body = dict(job.body or {})
        return {
            "request_id": job.request_id,
            "trace_id": str(body.get("trace_id") or ""),
            "job_kind": str(body.get("job_kind") or ("poll_step" if job.kind.startswith("poll_step_") else job.kind)),
            "priority": job.priority,
            "command_type": body.get("type"),
            "command_code": body.get("command_code"),
            "device_id": body.get("device_id") or "server-plc",
        }

    def _set_plc_trace_context(self, context: Optional[Dict[str, Any]]) -> None:
        setter = getattr(self._plc, "set_trace_context", None)
        if callable(setter):
            setter(context)

    def _log_io_error(self, *, job: PLCJob, exc: Exception, include_traceback: bool) -> None:
        if include_traceback:
            logger.exception("PLC-RT io job failed kind=%s error=%s", job.kind, exc)
            return

        now = time.monotonic()
        key = (str(job.kind), str(exc))
        should_log = key != self._last_io_error_key or (now - self._last_io_error_logged_at) >= 30.0
        if should_log:
            suffix = ""
            if key == self._last_io_error_key and self._io_error_suppressed:
                suffix = f" suppressed_repeats={self._io_error_suppressed}"
            logger.warning("PLC-RT io job failed kind=%s error=%s%s", job.kind, exc, suffix)
            self._last_io_error_key = key
            self._last_io_error_logged_at = now
            self._io_error_suppressed = 0
            return

        self._io_error_suppressed += 1
        logger.debug("PLC-RT io job failed kind=%s error=%s", job.kind, exc)
