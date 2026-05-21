from __future__ import annotations

import asyncio
import queue
import threading
from dataclasses import dataclass
from typing import Any, Callable, Generic, Optional, TypeVar

from sqlmodel import Session

from app.db.session import engine
from app.services.data_service import DataService


T = TypeVar("T")


@dataclass
class _DBTask(Generic[T]):
    func: Callable[[Session], T]
    done: threading.Event
    result: Optional[T] = None
    error: Optional[BaseException] = None


class DBWorkerError(RuntimeError):
    """Raised when DB worker execution fails."""


class DBWorker:
    def __init__(self, *, name: str = "db-worker", max_queue_size: int = 0) -> None:
        queue_size = int(max_queue_size or 0)
        self._queue: queue.Queue[Optional[_DBTask[Any]]] = queue.Queue(maxsize=max(0, queue_size))
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._name = str(name or "db-worker")

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name=self._name, daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop_event.set()
        self._queue.put(None)
        thread = self._thread
        self._thread = None
        if thread and thread.is_alive():
            thread.join(timeout=timeout)

    def call(self, func: Callable[[Session], T]) -> T:
        task: _DBTask[T] = _DBTask(func=func, done=threading.Event())
        self._queue.put(task)
        task.done.wait()
        if task.error is not None:
            raise DBWorkerError(str(task.error)) from task.error
        return task.result  # type: ignore[return-value]

    def call_data_service(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        def _invoke(session: Session) -> Any:
            service = DataService(session)
            method = getattr(service, method_name)
            return method(*args, **kwargs)

        return self.call(_invoke)

    async def call_async(self, func: Callable[[Session], T]) -> T:
        return await asyncio.to_thread(self.call, func)

    async def call_data_service_async(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        return await asyncio.to_thread(self.call_data_service, method_name, *args, **kwargs)

    def queue_size(self) -> int:
        return int(self._queue.qsize())

    def _run(self) -> None:
        while not self._stop_event.is_set():
            task = self._queue.get()
            if task is None:
                continue
            try:
                with Session(engine) as session:
                    task.result = task.func(session)
            except BaseException as exc:  # pragma: no cover - defensive
                task.error = exc
            finally:
                task.done.set()


class DataServiceProxy:
    def __init__(self, worker: DBWorker):
        self._worker = worker

    def __getattr__(self, name: str):
        return lambda *args, **kwargs: self._worker.call_data_service(name, *args, **kwargs)


class AsyncDataServiceProxy:
    def __init__(self, worker: DBWorker):
        self._worker = worker

    def __getattr__(self, name: str):
        async def _call(*args, **kwargs):
            return await self._worker.call_data_service_async(name, *args, **kwargs)

        return _call


_WRITE_METHOD_PREFIXES = (
    "create_",
    "upsert_",
    "update_",
    "delete_",
    "add_",
    "mark_",
    "prune_",
)


def _is_write_method(method_name: str) -> bool:
    name = str(method_name or "").strip()
    return name.startswith(_WRITE_METHOD_PREFIXES)


class RoutedAsyncDataServiceProxy:
    """Route DataService calls to read/write workers by method naming convention."""

    def __init__(self, *, read_worker: DBWorker, write_worker: DBWorker):
        self._read_worker = read_worker
        self._write_worker = write_worker

    def __getattr__(self, name: str):
        worker = self._write_worker if _is_write_method(name) else self._read_worker

        async def _call(*args, **kwargs):
            return await worker.call_data_service_async(name, *args, **kwargs)

        return _call
