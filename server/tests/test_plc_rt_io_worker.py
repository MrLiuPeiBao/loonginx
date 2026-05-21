from __future__ import annotations

import threading
import time

from app.plc_rt.io_worker import PLCIOWorker
from app.plc_rt.scheduler import PLCScheduler
from app.plc_rt.state_store import PLCStateStore
from app.services import cableway_plc


def _wait_until(predicate, timeout: float = 1.5) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _snapshot_events(events: list[dict]) -> list[dict]:
    return [event for event in events if event.get("kind") == "plc.status.snapshot"]


def test_plc_rt_poll_cycle_runs_all_steps_without_fault_query(monkeypatch):
    calls: list[str] = []
    emitted: list[dict] = []

    class FakePLC:
        def __init__(self, config):
            self.config = config
            self._trace_sink = None

        @property
        def status_poll_interval(self) -> float:
            return 0.05

        @property
        def heartbeat_interval(self) -> float:
            return 10.0

        @property
        def host(self) -> str:
            return "127.0.0.1"

        def close(self) -> None:
            return None

        def set_trace_sink(self, sink) -> None:
            self._trace_sink = sink

        def _maybe_send_heartbeat(self) -> None:
            calls.append("heartbeat")

        def read_motion_values(self, *, should_yield=None):
            calls.append("motion")
            return {
                "current_position_m": 1.5,
                "current_speed_mps": 0.2,
                "target_position_m": 5.0,
            }

        def read_current_task_code(self, *, should_yield=None):
            calls.append("task")
            return 401

        def read_status_bits(self, *, should_yield=None):
            calls.append("bits")
            return {
                "gz_total_fault": False,
                "zt_home_done": False,
                "zt_position_done": True,
            }

        def read_fault_details(self, *, should_yield=None):
            calls.append("faults")
            return {"gz_total_fault": True}

        def send_control_command(self, command_code: int, *, pulse: bool = True) -> None:
            calls.append(f"control-{command_code}")

        def send_estop(self, *, pulse: bool = True) -> None:
            calls.append("estop")

        def read_status(self):
            return {}

        def query_faults(self):
            return {}

    monkeypatch.setattr("app.plc_rt.io_worker.CablewayPLC", FakePLC)

    worker = PLCIOWorker(
        config=cableway_plc.CablewayPLCConfig(status_poll_interval=0.05, heartbeat_interval=10.0),
        scheduler=PLCScheduler(),
        state_store=PLCStateStore(),
        emit_event=emitted.append,
    )
    worker.start()
    try:
        assert _wait_until(lambda: bool(_snapshot_events(emitted)))
    finally:
        worker.stop()

    assert "motion" in calls
    assert "task" in calls
    assert "bits" in calls
    assert "faults" not in calls

    snapshot = _snapshot_events(emitted)[0]
    assert snapshot["body"]["reason"] == "poll_step"
    assert snapshot["body"]["snapshot"]["current_task_code"] == 401
    assert snapshot["body"]["snapshot"]["total_fault"] is False


def test_plc_rt_yields_poll_step_to_urgent_control_and_reschedules(monkeypatch):
    calls: list[str] = []
    emitted: list[dict] = []
    poll_started = threading.Event()

    class FakePLC:
        def __init__(self, config):
            self.config = config
            self._trace_sink = None
            self._motion_attempt = 0

        @property
        def status_poll_interval(self) -> float:
            return 0.05

        @property
        def heartbeat_interval(self) -> float:
            return 10.0

        @property
        def host(self) -> str:
            return "127.0.0.1"

        def close(self) -> None:
            return None

        def set_trace_sink(self, sink) -> None:
            self._trace_sink = sink

        def _maybe_send_heartbeat(self) -> None:
            calls.append("heartbeat")

        def read_motion_values(self, *, should_yield=None):
            self._motion_attempt += 1
            calls.append(f"motion-{self._motion_attempt}-start")
            if self._motion_attempt == 1:
                poll_started.set()
                deadline = time.time() + 0.3
                while time.time() < deadline:
                    if should_yield is not None and should_yield():
                        calls.append("motion-1-yield")
                        raise cableway_plc.CablewayPLCYieldToUrgentWork("motion")
                    time.sleep(0.01)
            calls.append(f"motion-{self._motion_attempt}-done")
            return {
                "current_position_m": 1.5,
                "current_speed_mps": 0.2,
                "target_position_m": 5.0,
            }

        def read_current_task_code(self, *, should_yield=None):
            calls.append("task")
            return 101

        def read_status_bits(self, *, should_yield=None):
            calls.append("bits")
            return {
                "gz_total_fault": False,
                "zt_home_done": False,
                "zt_position_done": False,
            }

        def read_fault_details(self, *, should_yield=None):
            calls.append("faults")
            return {"gz_total_fault": True}

        def send_control_command(self, command_code: int, *, pulse: bool = True) -> None:
            calls.append(f"control-{command_code}")
            if self._trace_sink is not None:
                self._trace_sink({"direction": "TX", "raw_hex": "00"})

        def send_estop(self, *, pulse: bool = True) -> None:
            calls.append("estop")

        def read_status(self):
            return {}

        def query_faults(self):
            return {}

    monkeypatch.setattr("app.plc_rt.io_worker.CablewayPLC", FakePLC)

    scheduler = PLCScheduler()
    worker = PLCIOWorker(
        config=cableway_plc.CablewayPLCConfig(status_poll_interval=0.05, heartbeat_interval=10.0),
        scheduler=scheduler,
        state_store=PLCStateStore(),
        emit_event=emitted.append,
    )
    worker.start()
    try:
        assert poll_started.wait(timeout=1.0)
        ticket = scheduler.submit_command(
            priority=0,
            kind="control",
            request_id="req-1",
            body={
                "type": "control",
                "command_code": 101,
                "pulse": True,
                "device_id": "server-plc",
            },
        )
        assert ticket.done.wait(timeout=1.0)
        assert _wait_until(lambda: bool(_snapshot_events(emitted)))
    finally:
        worker.stop()

    assert ticket.response is not None
    assert ticket.response["response_payload"]["success"] is True
    assert ticket.response["response_payload"]["result"] == "frame_sent"
    assert "motion-1-yield" in calls
    assert "control-101" in calls
    assert "motion-2-done" in calls
    assert calls.index("motion-1-yield") < calls.index("control-101") < calls.index("motion-2-done")
