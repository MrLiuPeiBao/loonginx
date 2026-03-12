from __future__ import annotations

from typing import Any, Dict


def with_payload_type(payload: Dict[str, Any], payload_type: str) -> Dict[str, Any]:
    """Attach payload_type field (in-place) and return payload."""
    if payload_type:
        payload["payload_type"] = str(payload_type)
    return payload
