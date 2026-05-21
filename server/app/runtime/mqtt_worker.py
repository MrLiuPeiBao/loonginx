from __future__ import annotations

import asyncio
import queue
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from app.core.config import Settings
from app.mqtt import MQTTManager, MQTTMessageContext


MQTTHandler = Callable[[MQTTMessageContext], None]


@dataclass
class _PublishTask:
    topic: str
    payload: bytes
    qos: int
    retain: bool
    done: threading.Event
    result: Optional[bool] = None
    error: Optional[BaseException] = None


@dataclass
class _MessageTask:
    handler: MQTTHandler
    context: MQTTMessageContext


@dataclass
class _SettingsTask:
    settings: Settings


class MQTTWorker:
    def __init__(self, settings: Settings):
        self._manager = MQTTManager(settings)
        self._queue: queue.Queue[object] = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._handlers: Dict[str, MQTTHandler] = {}

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="mqtt-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop_event.set()
        self._queue.put(None)
        thread = self._thread
        self._thread = None
        self._manager.disconnect()
        if thread and thread.is_alive():
            thread.join(timeout=timeout)

    def connect(self) -> None:
        self.start()

    def disconnect(self) -> None:
        self.stop()

    def apply_settings(self, settings: Settings) -> None:
        self._queue.put(_SettingsTask(settings=settings))

    @property
    def is_connected(self) -> bool:
        return self._manager.is_connected

    def register_handler(self, topic: str, handler: MQTTHandler, qos: int = 0) -> None:
        self._handlers[topic] = handler
        self._manager.register_handler(topic, handler, qos=qos)

    def unregister_handler(self, topic: str) -> None:
        self._handlers.pop(topic, None)
        self._manager.unregister_handler(topic)

    def publish(self, topic: str, payload: bytes, qos: int = 0, retain: bool = False) -> bool:
        self.start()
        if threading.current_thread() is self._thread:
            return self._manager.publish(topic, payload=payload, qos=qos, retain=retain)
        task = _PublishTask(
            topic=topic,
            payload=payload,
            qos=qos,
            retain=retain,
            done=threading.Event(),
        )
        self._queue.put(task)
        task.done.wait()
        if task.error:
            raise task.error
        return bool(task.result)

    async def publish_async(self, topic: str, payload: bytes, qos: int = 0, retain: bool = False) -> bool:
        return await asyncio.to_thread(self.publish, topic, payload, qos, retain)

    def _run(self) -> None:
        next_connect_attempt = 0.0
        while not self._stop_event.is_set():
            if not self._manager.is_connected and time.monotonic() >= next_connect_attempt:
                self._manager.connect_foreground()
                next_connect_attempt = time.monotonic() + 5.0
            self._manager.loop_once(0.1)
            try:
                task = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if task is None:
                continue
            if isinstance(task, _SettingsTask):
                self._manager.apply_settings(task.settings, start_loop_thread=False)
                next_connect_attempt = 0.0
                continue
            if isinstance(task, _PublishTask):
                try:
                    task.result = self._manager.publish(
                        task.topic,
                        payload=task.payload,
                        qos=task.qos,
                        retain=task.retain,
                    )
                except BaseException as exc:  # pragma: no cover - defensive
                    task.error = exc
                finally:
                    task.done.set()
