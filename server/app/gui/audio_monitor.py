from __future__ import annotations

from typing import Any, Dict, List

import httpx


class AudioRTSPMonitor:
    """Small GUI-side client for recent audio metrics."""

    def __init__(self, *, base_url: str = 'http://127.0.0.1:8000', timeout: float = 3.0) -> None:
        self._base_url = str(base_url or 'http://127.0.0.1:8000').rstrip('/')
        self._timeout = float(timeout or 3.0)

    def get_latest(self) -> List[Dict[str, Any]]:
        try:
            response = httpx.get(f'{self._base_url}/api/audio/metrics', params={'limit': 100}, timeout=self._timeout)
            response.raise_for_status()
            payload = response.json()
        except Exception:
            return []
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            return [payload]
        return []

