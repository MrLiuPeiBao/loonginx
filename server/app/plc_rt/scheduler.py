from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ResponseTicket:
    done: threading.Event = field(default_factory=threading.Event)
    response: Optional[dict[str, Any]] = None


@dataclass(order=True)
class PLCJob:
    priority: int
    sequence: int
    kind: str = field(compare=False)
    request_id: Optional[str] = field(default=None, compare=False)
    body: dict[str, Any] = field(default_factory=dict, compare=False)
    ticket: Optional[ResponseTicket] = field(default=None, compare=False)
    enqueued_mono_ns: int = field(default_factory=time.perf_counter_ns, compare=False)


class PLCScheduler:
    def __init__(self) -> None:
        self._queue: queue.PriorityQueue[PLCJob] = queue.PriorityQueue()
        self._lock = threading.Lock()
        self._sequence = 0
        self._poll_depth = 0

    def submit_command(self, *, priority: int, kind: str, request_id: Optional[str], body: dict[str, Any]) -> ResponseTicket:
        ticket = ResponseTicket()
        self._queue.put(PLCJob(priority=priority, sequence=self._next_sequence(), kind=kind, request_id=request_id, body=dict(body), ticket=ticket))
        return ticket

    def submit_internal(self, *, priority: int, kind: str, body: dict[str, Any]) -> None:
        self._queue.put(PLCJob(priority=priority, sequence=self._next_sequence(), kind=kind, body=dict(body)))

    def schedule_poll_step(self, *, step_name: str, body: Optional[dict[str, Any]] = None) -> None:
        with self._lock:
            if self._poll_depth > 0:
                return
            self._poll_depth = 1
        self._queue.put(PLCJob(priority=30, sequence=self._next_sequence(), kind=step_name, body=dict(body or {})))

    def continue_poll_step(self, *, step_name: str, body: Optional[dict[str, Any]] = None) -> None:
        with self._lock:
            self._poll_depth += 1
        self._queue.put(PLCJob(priority=30, sequence=self._next_sequence(), kind=step_name, body=dict(body or {})))

    def mark_poll_consumed(self) -> None:
        with self._lock:
            self._poll_depth = max(0, self._poll_depth - 1)

    def get(self, timeout: float = 0.1) -> PLCJob:
        return self._queue.get(timeout=timeout)

    def task_done(self) -> None:
        self._queue.task_done()

    def qsize(self) -> int:
        return self._queue.qsize()

    def urgent_pending(self) -> bool:
        with self._lock:
            queued = list(self._queue.queue)
        return any(job.priority <= 10 for job in queued)

    def _next_sequence(self) -> int:
        with self._lock:
            self._sequence += 1
            return self._sequence
