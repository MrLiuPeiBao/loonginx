from __future__ import annotations

import json
import time
import uuid
from typing import Any, Dict


IPC_VERSION = 1


def new_message_id(prefix: str = "ipc") -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def make_message(*, kind: str, message_id: str, source: str, body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "v": IPC_VERSION,
        "kind": str(kind),
        "id": str(message_id),
        "source": str(source),
        "ts_unix_ms": int(time.time() * 1000),
        "body": dict(body or {}),
    }


def encode_message(message: Dict[str, Any]) -> bytes:
    return json.dumps(message, ensure_ascii=False).encode("utf-8")


def decode_message(payload: bytes) -> Dict[str, Any]:
    decoded = json.loads(bytes(payload or b"{}").decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("IPC message must be a JSON object")
    return decoded
