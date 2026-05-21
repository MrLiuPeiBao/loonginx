"""MQTT command response persistence helpers."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Optional, TYPE_CHECKING

from app.db.models import CommandDirection, CommandStatus
from app.services.data_service import DataService

if TYPE_CHECKING:
    from app.mqtt import MQTTMessageContext
    from app.runtime.db_worker import DBWorker


logger = logging.getLogger(__name__)
_BOOL_TRUE_VALUES = {"1", "true", "yes", "on"}
_BOOL_FALSE_VALUES = {"0", "false", "no", "off"}


def bytes_to_hex(data: bytes) -> str:
    """Convert bytes into an uppercase, space-delimited hex string."""
    return " ".join(f"{byte:02X}" for byte in data)


def _parse_success(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if value in (0, 1):
            return bool(value)
        return None
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in _BOOL_TRUE_VALUES:
        return True
    if text in _BOOL_FALSE_VALUES:
        return False
    return None


def handle_command_response_payload(
    context: "MQTTMessageContext",
    *,
    db_worker: Optional["DBWorker"] = None,
) -> None:
    """Persist MQTT command responses and update request lifecycle when possible."""
    payload_hex = bytes_to_hex(context.payload)
    payload_text: Optional[str] = None
    payload_json: Optional[dict[str, Any]] = None

    try:
        payload_text = context.payload.decode("utf-8")
        decoded = json.loads(payload_text)
        if isinstance(decoded, dict):
            payload_json = decoded
    except Exception:
        payload_text = None

    payload_for_log = payload_hex
    notes = [f"MQTT:{context.topic}"]
    device_id = None
    request_id = None
    status: Optional[CommandStatus] = None
    error: Optional[str] = None

    if payload_json is not None:
        request_hex = payload_json.get("request_hex") or payload_json.get("command")
        response_hex = payload_json.get("response_hex") or payload_json.get("response")
        success = _parse_success(payload_json.get("success"))
        device_id = payload_json.get("device_id")
        request_id = payload_json.get("request_id")
        error = str(payload_json.get("error") or "").strip() or None
        payload_for_log = response_hex or payload_text or payload_hex
        if request_id:
            notes.append(f"request_id={request_id}")
        if success is not None:
            notes.append(f"success={success}")
            status = CommandStatus.ACK if success else CommandStatus.FAILED
        elif error:
            status = CommandStatus.FAILED
        if request_hex:
            notes.append(f"request={request_hex}")
        if error:
            notes.append(f"error={error}")
    elif payload_text:
        payload_for_log = payload_text

    try:
        def _persist(session):
            data_service = DataService(session)
            data_service.add_command_log(
                timestamp=datetime.now(),
                direction=CommandDirection.RESPONSE,
                payload=str(payload_for_log),
                notes=" | ".join(notes),
                device_id=device_id,
            )
            if request_id and status is not None:
                data_service.update_command_request_status(
                    request_id=request_id,
                    status=status,
                    response_payload=payload_text or str(payload_for_log),
                    error=error,
                    device_id=device_id,
                )

        if db_worker is None:
            logger.error("Skipping MQTT command response persistence because db_worker is not configured")
            return
        db_worker.call(_persist)
    except Exception:  # pragma: no cover - external DB dependency
        logger.exception("Failed to persist MQTT command response")
