from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Generic, Optional, TypeVar


T = TypeVar('T')


@dataclass
class _Box(Generic[T]):
    value: T


class LatestCache(Generic[T]):
    """Thread-safe in-memory single-value cache."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._box: Optional[_Box[T]] = None

    def set(self, value: T) -> None:
        with self._lock:
            self._box = _Box(value=value)

    def get(self) -> Optional[T]:
        with self._lock:
            if self._box is None:
                return None
            return self._box.value

