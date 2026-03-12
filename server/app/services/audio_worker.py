"""Standalone audio worker process for isolation from API."""

from __future__ import annotations

import signal
import threading
import time

from app.core.config import get_settings, load_env
from app.mqtt import MQTTManager
from app.services.audio_service import AudioMonitorService


def main() -> None:
    load_env()
    settings = get_settings()
    mqtt_manager = MQTTManager(settings)
    mqtt_manager.connect()
    service = AudioMonitorService(settings, mqtt_manager=mqtt_manager)
    service.start()

    stop_event = threading.Event()

    def _handle_signal(_sig, _frame) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    try:
        while not stop_event.is_set():
            time.sleep(1.0)
    finally:
        service.stop()
        mqtt_manager.disconnect()


if __name__ == '__main__':
    main()
