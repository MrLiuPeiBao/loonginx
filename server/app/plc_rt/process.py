from __future__ import annotations

import json
import queue
import signal
import threading
import time
from typing import Any, Dict

from app.core.config import get_settings, load_env
from app.ipc import NamedPipeJsonClient, NamedPipeJsonServer
from app.ipc.protocol import make_message
from app.plc_rt.events import build_command_response
from app.plc_rt.io_worker import PLCIOWorker
from app.plc_rt.scheduler import PLCScheduler
from app.plc_rt.state_store import PLCStateStore
from app.services.cableway_plc import CablewayPLCConfig
from app.services.plc_timing import emit_plc_timing, new_trace_id


def main() -> None:
    load_env()
    settings = get_settings()
    scheduler = PLCScheduler()
    state_store = PLCStateStore()
    stop_event = threading.Event()
    event_queue: "queue.Queue[Dict[str, Any]]" = queue.Queue()

    def _emit_event(event: Dict[str, Any]) -> None:
        event_queue.put(dict(event))

    io_worker = PLCIOWorker(
        config=CablewayPLCConfig(
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
        ),
        scheduler=scheduler,
        state_store=state_store,
        emit_event=_emit_event,
    )
    io_worker.start()

    def _handle_signal(_sig, _frame) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    cmd_server = NamedPipeJsonServer(
        address=str(settings.plc_rt_pipe_name),
        authkey=str(settings.plc_rt_authkey or "loonginx-plc-rt").encode("utf-8"),
    )
    evt_client = NamedPipeJsonClient(
        address=str(settings.plc_evt_pipe_name),
        authkey=str(settings.plc_evt_authkey or "loonginx-plc-evt").encode("utf-8"),
        request_timeout=2.0,
        connect_retry_seconds=0.5,
    )

    def _command_handler(message: Dict[str, object]) -> Dict[str, object]:
        body = message.get("body")
        if not isinstance(body, dict):
            return build_command_response(
                message_id=str(message.get("id") or "unknown"),
                result="rejected",
                error="invalid body",
                execution={
                    "response_payload": {"success": False, "error": "invalid body"},
                    "status_payload": None,
                    "frames": [],
                },
            )

        command_type = str(body.get("type") or "")
        request_id = body.get("request_id") if isinstance(body.get("request_id"), str) else None
        trace_id = str(body.get("trace_id") or "").strip() or new_trace_id("plc")
        command_code = body.get("command_code")
        device_id = body.get("device_id")
        if command_type in {"control", "estop"}:
            priority = 0
        elif command_type in {"read_status", "query_faults"}:
            priority = 10
        else:
            return build_command_response(
                message_id=str(message.get("id") or "unknown"),
                result="rejected",
                error=f"unsupported command type={command_type}",
                execution={
                    "response_payload": {"success": False, "error": f"unsupported command type={command_type}"},
                    "status_payload": None,
                    "frames": [],
                },
            )

        emit_plc_timing(
            "svc.execute_enter",
            request_id=request_id,
            trace_id=trace_id,
            job_kind="command",
            priority=priority,
            command_type=command_type,
            command_code=command_code,
            device_id=device_id,
        )
        ticket = scheduler.submit_command(
            priority=priority,
            kind=command_type,
            request_id=request_id,
            body={**dict(body), "trace_id": trace_id},
        )
        emit_plc_timing(
            "svc.enqueue",
            request_id=request_id,
            trace_id=trace_id,
            job_kind="command",
            priority=priority,
            queue_depth=scheduler.qsize(),
            urgent_waiting=scheduler.urgent_pending(),
            command_type=command_type,
            command_code=command_code,
            device_id=device_id,
        )
        timeout_seconds = max(5.0, float(settings.plc_rt_request_timeout or 5.0))
        if not ticket.done.wait(timeout=timeout_seconds):
            emit_plc_timing(
                "svc.execute_exit",
                request_id=request_id,
                trace_id=trace_id,
                job_kind="command",
                priority=priority,
                command_type=command_type,
                command_code=command_code,
                device_id=device_id,
                result="timeout",
                error=f"Timed out after {timeout_seconds:.1f}s",
            )
            return build_command_response(
                message_id=str(message.get("id") or "unknown"),
                result="timeout",
                error=f"Timed out after {timeout_seconds:.1f}s",
                execution={
                    "response_payload": {"success": False, "error": f"Timed out after {timeout_seconds:.1f}s"},
                    "status_payload": None,
                    "frames": [],
                },
            )
        execution = dict(ticket.response or {})
        response_payload = execution.get("response_payload")
        result = "frame_sent" if isinstance(response_payload, dict) and response_payload.get("success") else "plc_error"
        emit_plc_timing(
            "svc.execute_exit",
            request_id=request_id,
            trace_id=trace_id,
            job_kind="command",
            priority=priority,
            command_type=command_type,
            command_code=command_code,
            device_id=device_id,
            result=result,
            error=None if result == "frame_sent" else str(response_payload.get("error") if isinstance(response_payload, dict) else "plc_error"),
        )
        return build_command_response(
            message_id=str(message.get("id") or body.get("request_id") or "unknown"),
            result=result,
            error=None if result == "frame_sent" else str(response_payload.get("error") if isinstance(response_payload, dict) else "plc_error"),
            execution=execution,
        )

    def _event_thread_main() -> None:
        cmd_server.serve_forever(handler=_command_handler, should_stop=stop_event.is_set)

    event_thread = threading.Thread(target=_event_thread_main, name="plc-event-thread", daemon=True)
    event_thread.start()

    try:
        while not stop_event.is_set():
            try:
                event = event_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                evt_client.request(event)
            except Exception:
                _ = json.dumps(event, ensure_ascii=False)
    finally:
        io_worker.stop()
        stop_event.set()
        event_thread.join(timeout=1.0)


if __name__ == "__main__":
    main()
