from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple


@dataclass
class SnapshotUpdate:
    seq: int
    snapshot: Dict[str, Any]
    raised: List[str] = field(default_factory=list)
    cleared: List[str] = field(default_factory=list)


class PLCStateStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._seq = 0
        self._snapshot: Dict[str, Any] = {}
        self._active_faults: set[str] = set()

    def snapshot(self) -> Tuple[int, Dict[str, Any]]:
        with self._lock:
            return self._seq, dict(self._snapshot)

    def update(self, snapshot: Dict[str, Any]) -> SnapshotUpdate:
        current_faults = {
            str(key)
            for key, enabled in dict(snapshot.get("faults") or {}).items()
            if enabled
        }
        with self._lock:
            previous_faults = set(self._active_faults)
            self._seq += 1
            self._snapshot = dict(snapshot)
            self._active_faults = current_faults
            return SnapshotUpdate(
                seq=self._seq,
                snapshot=dict(self._snapshot),
                raised=sorted(current_faults - previous_faults),
                cleared=sorted(previous_faults - current_faults),
            )
