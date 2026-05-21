from __future__ import annotations

import time
from pathlib import Path

from app.services.plc_timing import (
    build_timing_summary,
    emit_plc_timing,
    load_plc_timing_events,
)


def test_emit_and_load_plc_timing_events(monkeypatch, tmp_path):
    log_path = tmp_path / "plc_timing.log"
    monkeypatch.setenv("PLC_TIMING_TRACE_ENABLED", "true")
    monkeypatch.setenv("PLC_TIMING_TRACE_LOG", str(log_path))

    emit_plc_timing("gui.click", request_id="req-1", trace_id="trace-1", command_type="control")
    emit_plc_timing("modbus.tx_send", request_id="req-1", trace_id="trace-1", modbus_tx_id=1)

    events = load_plc_timing_events(log_path)
    assert len(events) == 2
    assert events[0]["request_id"] == "req-1"
    assert events[0]["trace_id"] == "trace-1"
    assert events[0]["stage"] == "gui.click"
    assert events[1]["stage"] == "modbus.tx_send"


def test_build_timing_summary_reports_click_to_tx_send():
    start_wall = 1_000_000_000
    events = [
        {
            "kind": "plc_timing",
            "request_id": "req-1",
            "trace_id": "trace-1",
            "stage": "gui.click",
            "wall_time_ns": start_wall,
            "mono_ns": 100,
            "process_id": 1,
            "thread_name": "gui-main",
        },
        {
            "kind": "plc_timing",
            "request_id": "req-1",
            "trace_id": "trace-1",
            "stage": "api.command_log_db_start",
            "wall_time_ns": start_wall + 5_000_000,
            "mono_ns": 200,
            "process_id": 2,
            "thread_name": "api-main",
        },
        {
            "kind": "plc_timing",
            "request_id": "req-1",
            "trace_id": "trace-1",
            "stage": "api.command_log_db_end",
            "wall_time_ns": start_wall + 8_000_000,
            "mono_ns": 210,
            "process_id": 2,
            "thread_name": "api-main",
        },
        {
            "kind": "plc_timing",
            "request_id": "req-1",
            "trace_id": "trace-1",
            "stage": "svc.dequeue",
            "wall_time_ns": start_wall + 12_000_000,
            "mono_ns": 300,
            "process_id": 3,
            "thread_name": "plc-io-thread",
            "queue_wait_ns": 4_000_000,
        },
        {
            "kind": "plc_timing",
            "request_id": "req-1",
            "trace_id": "trace-1",
            "stage": "modbus.tx_send",
            "wall_time_ns": start_wall + 25_000_000,
            "mono_ns": 400,
            "process_id": 3,
            "thread_name": "plc-io-thread",
        },
    ]

    summary = build_timing_summary(events)
    assert summary["click_to_tx_send_ms"] == 25.0
    assert summary["db_prelog_ms"] == 3.0
    assert summary["queue_wait_ms"] == 4.0


def test_emit_skips_poll_when_disabled(monkeypatch, tmp_path):
    log_path = tmp_path / "plc_timing.log"
    monkeypatch.setenv("PLC_TIMING_TRACE_ENABLED", "true")
    monkeypatch.setenv("PLC_TIMING_TRACE_LOG", str(log_path))
    monkeypatch.setenv("PLC_TIMING_TRACE_INCLUDE_POLL", "false")

    emit_plc_timing("svc.poll_start", request_id="req-poll", trace_id="trace-poll", job_kind="poll_step")
    emit_plc_timing("svc.command_start", request_id="req-cmd", trace_id="trace-cmd", job_kind="command")

    events = load_plc_timing_events(log_path)
    stages = [event["stage"] for event in events]
    assert "svc.poll_start" not in stages
    assert "svc.command_start" in stages
