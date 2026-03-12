from __future__ import annotations

from typing import Optional, Tuple


def update_degraded_state(
    *,
    connected: bool,
    now: float,
    degraded: bool,
    disconnected_since: Optional[float],
    connected_since: Optional[float],
    enter_after: float,
    exit_after: float,
) -> Tuple[bool, Optional[float], Optional[float]]:
    """Update degraded state based on connectivity and timing."""
    if connected:
        disconnected_since = None
        if connected_since is None:
            connected_since = now
        if degraded and now - connected_since >= exit_after:
            degraded = False
    else:
        connected_since = None
        if disconnected_since is None:
            disconnected_since = now
        if now - disconnected_since >= enter_after:
            degraded = True
    return degraded, disconnected_since, connected_since
