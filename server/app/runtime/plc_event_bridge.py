from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from app.core.config import Settings
from app.core.constants import MQTT_TOPICS
from app.ipc import NamedPipeJsonServer, make_message
from app.runtime.db_worker import DBWorker
from app.runtime.mqtt_worker import MQTTWorker
from app.services.cableway_ingestion import handle_cableway_status_payload


logger = logging.getLogger(__name__)


class PLCEventBridgeServer:
    def __init__(self, *, settings: Settings, db_worker: DBWorker, mqtt_worker: MQTTWorker):
        self._settings = settings
        self._db_worker = db_worker
        self._mqtt_worker = mqtt_worker
        self._server = NamedPipeJsonServer(
            address=str(settings.plc_evt_pipe_name),
            authkey=str(settings.plc_evt_authkey or "loonginx-plc-evt").encode("utf-8"),
        )
        self._stop = False

    def start(self) -> None:
        if getattr(self, "_thread", None) and self._thread.is_alive():
            return
        import threading

        self._stop = False
        self._thread = threading.Thread(target=self._run, name="plc-event-bridge", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop = True
        thread = getattr(self, "_thread", None)
        self._thread = None
        if thread and thread.is_alive():
            thread.join(timeout=timeout)

    def _run(self) -> None:
        try:
            self._server.serve_forever(handler=self._handle_message, should_stop=lambda: self._stop)
        except PermissionError as exc:
            if self._stop:
                return
            logger.warning(
                "PLC event bridge unavailable; named pipe is likely owned by another API process: %s",
                exc,
            )

    def _handle_message(self, message: Dict[str, object]) -> Dict[str, object]:
        kind = str(message.get("kind") or "")
        body = message.get("body")
        if not isinstance(body, dict):
            return make_message(kind="plc.event.ack", message_id=str(message.get("id") or "unknown"), source="api", body={"ok": False, "error": "invalid body"})

        if kind == "plc.status.snapshot":
            snapshot = body.get("snapshot")
            if isinstance(snapshot, dict):
                payload = {
                    "payload_type": "cableway_status",
                    "device_id": self._settings.plc_device_id,
                    "location": self._settings.plc_location,
                    "timestamp": snapshot.get("timestamp"),
                    "ts": snapshot.get("ts"),
                    "schema": 1,
                    "plc_host": snapshot.get("plc_host") or self._settings.plc_host,
                    "status": snapshot,
                }
                context = type("Context", (), {
                    "topic": MQTT_TOPICS["cableway_status"],
                    "payload": __import__("json").dumps(payload, ensure_ascii=False).encode("utf-8"),
                    "qos": 0,
                    "retain": False,
                })()
                handle_cableway_status_payload(context, mqtt_manager=self._mqtt_worker, db_worker=self._db_worker)
        return make_message(kind="plc.event.ack", message_id=str(message.get("id") or "unknown"), source="api", body={"ok": True})
