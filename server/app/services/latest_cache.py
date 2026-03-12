from __future__ import annotations

from threading import Lock
from typing import Generic, Optional, TypeVar

T = TypeVar('T')


class LatestCache(Generic[T]):
    def __init__(self) -> None:
        self._lock = Lock()
        self._value: Optional[T] = None

    def set(self, value: T) -> None:
        with self._lock:
            self._value = value

    def get(self) -> Optional[T]:
        with self._lock:
            return self._value
