from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Optional

from app.core.config import Settings
from app.ipc import NamedPipeJsonClient, NamedPipeJsonServer, make_message
from app.runtime.media_write_worker import MediaWriteWorker


logger = logging.getLogger(__name__)


class TelemetryBridgeClient:
    def __init__(self, settings: Settings):
        self._client = NamedPipeJsonClient(
            address=str(settings.telemetry_pipe_name),
            authkey=str(settings.telemetry_authkey or "loonginx-telemetry").encode("utf-8"),
            request_timeout=5.0,
            connect_retry_seconds=1.0,
        )

    def emit(self, *, kind: str, source: str, body: Dict[str, Any]) -> Dict[str, Any]:
        message_id = str(body.get("request_id") or body.get("track_id") or kind)
        message = make_message(
            kind=kind,
            message_id=message_id,
            source=source,
            body=body,
        )
        return self._client.request(message)


class TelemetryBridgeServer:
    def __init__(
        self,
        *,
        settings: Settings,
        media_write_worker: MediaWriteWorker,
    ):
        self._settings = settings
        self._media_write_worker = media_write_worker
        self._server = NamedPipeJsonServer(
            address=str(settings.telemetry_pipe_name),
            authkey=str(settings.telemetry_authkey or "loonginx-telemetry").encode("utf-8"),
        )
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="telemetry-bridge", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop_event.set()
        thread = self._thread
        self._thread = None
        if thread and thread.is_alive():
            thread.join(timeout=timeout)

    def _run(self) -> None:
        try:
            self._server.serve_forever(handler=self._handle_message, should_stop=self._stop_event.is_set)
        except PermissionError as exc:
            if self._stop_event.is_set():
                return
            logger.warning(
                "Telemetry bridge unavailable; named pipe is likely owned by another API process: %s",
                exc,
            )

    def _handle_message(self, message: Dict[str, object]) -> Dict[str, object]:
        kind = str(message.get("kind") or "")
        body = message.get("body")
        if not isinstance(body, dict):
            return make_message(
                kind="telemetry.resp",
                message_id=str(message.get("id") or "unknown"),
                source="api",
                body={"ok": False, "error": "invalid body"},
            )
        if kind not in {"telemetry.yolo.snapshot", "telemetry.audio.clip"}:
            return make_message(
                kind="telemetry.resp",
                message_id=str(message.get("id") or "unknown"),
                source="api",
                body={"ok": False, "error": f"unsupported telemetry kind={kind}"},
            )
        accepted = self._media_write_worker.enqueue(kind=kind, body=body)
        if not accepted:
            return make_message(
                kind="telemetry.resp",
                message_id=str(message.get("id") or "unknown"),
                source="api",
                body={"ok": False, "error": "media queue is full"},
            )
        return make_message(
            kind="telemetry.resp",
            message_id=str(message.get("id") or "unknown"),
            source="api",
            body={"ok": True},
        )
