from __future__ import annotations

import contextlib
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, Optional


_SERVER_ROOT = Path(__file__).resolve().parents[2]
_THREAD_LOCAL = threading.local()
_POLL_JOB_KINDS = {"poll", "poll_step", "heartbeat"}


def new_trace_id(prefix: str = "plc") -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def timing_enabled() -> bool:
    return _env_bool("PLC_TIMING_TRACE_ENABLED", default=False)


def timing_include_poll() -> bool:
    return _env_bool("PLC_TIMING_TRACE_INCLUDE_POLL", default=True)


def timing_include_headers() -> bool:
    return _env_bool("PLC_TIMING_TRACE_INCLUDE_HEADERS", default=True)


def timing_log_path() -> Path:
    raw = str(os.getenv("PLC_TIMING_TRACE_LOG", "logs/plc_timing.log") or "logs/plc_timing.log").strip()
    path = Path(raw)
    if not path.is_absolute():
        path = _SERVER_ROOT / path
    return path


def bind_plc_timing_context(**context: Any) -> None:
    cleaned = {key: _jsonable(value) for key, value in context.items() if value is not None}
    setattr(_THREAD_LOCAL, "plc_timing_context", cleaned)


def clear_plc_timing_context() -> None:
    if hasattr(_THREAD_LOCAL, "plc_timing_context"):
        delattr(_THREAD_LOCAL, "plc_timing_context")


def get_plc_timing_context() -> Dict[str, Any]:
    value = getattr(_THREAD_LOCAL, "plc_timing_context", None)
    return dict(value) if isinstance(value, dict) else {}


@contextlib.contextmanager
def plc_timing_context(**context: Any) -> Iterator[None]:
    previous = get_plc_timing_context()
    merged = dict(previous)
    merged.update({key: value for key, value in context.items() if value is not None})
    bind_plc_timing_context(**merged)
    try:
        yield
    finally:
        if previous:
            bind_plc_timing_context(**previous)
        else:
            clear_plc_timing_context()


@contextlib.contextmanager
def time_block(
    stage_start: str,
    stage_end: str,
    request_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    **fields: Any,
) -> Iterator[None]:
    start = emit_plc_timing(stage_start, request_id=request_id, trace_id=trace_id, **fields)
    try:
        yield
    finally:
        end_fields = dict(fields)
        if start is not None:
            end_fields["elapsed_ns"] = max(0, time.perf_counter_ns() - int(start["mono_ns"]))
        emit_plc_timing(stage_end, request_id=request_id, trace_id=trace_id, **end_fields)


def emit_plc_timing(
    stage: str,
    request_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    **fields: Any,
) -> Optional[Dict[str, Any]]:
    if not timing_enabled():
        return None

    merged = get_plc_timing_context()
    merged.update({key: value for key, value in fields.items() if value is not None})

    event_request_id = _coerce_text(request_id) or _coerce_text(merged.get("request_id"))
    event_trace_id = _coerce_text(trace_id) or _coerce_text(merged.get("trace_id"))

    if _should_skip(stage, merged):
        return None

    event: Dict[str, Any] = {
        "kind": "plc_timing",
        "stage": str(stage),
        "request_id": event_request_id,
        "trace_id": event_trace_id,
        "wall_time_ns": time.time_ns(),
        "mono_ns": time.perf_counter_ns(),
        "process_id": os.getpid(),
        "thread_name": threading.current_thread().name,
    }
    for key, value in merged.items():
        if key in {"request_id", "trace_id"}:
            continue
        if value is None:
            continue
        event[key] = _jsonable(value)

    _append_event(event)
    return event


def load_plc_timing_events(path: Optional[Path] = None) -> list[Dict[str, Any]]:
    log_path = path or timing_log_path()
    if not log_path.is_file():
        return []
    events: list[Dict[str, Any]] = []
    with log_path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            try:
                decoded = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(decoded, dict):
                events.append(decoded)
    return events


def build_timing_summary(events: Iterable[Dict[str, Any]]) -> Dict[str, Optional[float]]:
    ordered = sorted(
        [dict(event) for event in events if isinstance(event, dict)],
        key=lambda item: int(item.get("wall_time_ns") or 0),
    )
    if not ordered:
        return {
            "click_to_tx_send_ms": None,
            "gui_http_rtt_ms": None,
            "api_pre_execute_ms": None,
            "db_prelog_ms": None,
            "queue_wait_ms": None,
            "connect_ms": None,
            "primary_write_ms": None,
            "plc_response_ms": None,
            "pulse_reset_ms": None,
            "status_followup_ms": None,
            "api_post_execute_ms": None,
        }

    def _first(stage: str) -> Optional[Dict[str, Any]]:
        return next((event for event in ordered if event.get("stage") == stage), None)

    def _duration_ms(start_stage: str, end_stage: str) -> Optional[float]:
        start = _first(start_stage)
        end = _first(end_stage)
        if start is None or end is None:
            return None
        delta = int(end.get("wall_time_ns") or 0) - int(start.get("wall_time_ns") or 0)
        if delta < 0:
            return None
        return round(delta / 1_000_000.0, 3)

    dequeue = _first("svc.dequeue")
    queue_wait_ms = None
    if dequeue is not None and dequeue.get("queue_wait_ns") is not None:
        queue_wait_ms = round(float(dequeue["queue_wait_ns"]) / 1_000_000.0, 3)

    status_end_stage = None
    for candidate in (
        "plc.read_fault_window_end",
        "plc.read_status_bits_end",
        "plc.read_task_end",
        "plc.read_motion_end",
    ):
        if _first(candidate) is not None:
            status_end_stage = candidate

    return {
        "click_to_tx_send_ms": _duration_ms("gui.click", "modbus.tx_send"),
        "gui_http_rtt_ms": _duration_ms("gui.http_send_start", "gui.http_response_received"),
        "api_pre_execute_ms": _duration_ms("api.route_enter", "api.execute_command_call_start"),
        "db_prelog_ms": _duration_ms("api.command_log_db_start", "api.command_log_db_end"),
        "queue_wait_ms": queue_wait_ms,
        "connect_ms": _duration_ms("modbus.connect_start", "modbus.connect_end"),
        "primary_write_ms": _duration_ms("plc.control_start", "plc.control_primary_write_done"),
        "plc_response_ms": _duration_ms("modbus.tx_send", "modbus.request_end"),
        "pulse_reset_ms": _duration_ms("plc.pulse_sleep_start", "plc.control_reset_write_done"),
        "status_followup_ms": _duration_ms("plc.read_status_start", status_end_stage) if status_end_stage else None,
        "api_post_execute_ms": _duration_ms("api.execute_command_call_end", "api.response_ready"),
    }


def _append_event(event: Dict[str, Any]) -> None:
    path = timing_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
    fd = os.open(str(path), os.O_APPEND | os.O_CREAT | os.O_WRONLY)
    try:
        os.write(fd, payload.encode("utf-8"))
    finally:
        os.close(fd)


def _should_skip(stage: str, fields: Dict[str, Any]) -> bool:
    if timing_include_poll():
        return False
    if str(stage).startswith("svc.poll"):
        return True
    if str(fields.get("job_kind") or "").strip().lower() in _POLL_JOB_KINDS:
        return True
    return False


def _env_bool(name: str, *, default: bool) -> bool:
    raw = str(os.getenv(name, str(default)) or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return str(value)
