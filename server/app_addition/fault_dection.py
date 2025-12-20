"""Base fault detection class for conveyor belt monitoring."""

from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from app.mqtt import MQTTManager
    from app.core.config import Settings

logger = logging.getLogger(__name__)


class FaultDetection(ABC):
    """Abstract base class for all fault detection systems."""

    def __init__(
        self,
        name: str,
        settings: Optional["Settings"] = None,
        mqtt_manager: Optional["MQTTManager"] = None,
    ):
        """Initialize fault detection system.

        Args:
            name (str): Name identifier for this detection system.
            settings (Optional[Settings]): Application settings.
            mqtt_manager (Optional[MQTTManager]): MQTT manager for publishing alarms.
        """
        self.name = name
        self.settings = settings
        self.mqtt_manager = mqtt_manager
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._is_running = False
        logger.info('Fault detection system "%s" initialized', self.name)

    @property
    def enabled(self) -> bool:
        """Check whether detection system can be started.

        Returns:
            bool: True if system is enabled and configured properly.
        """
        return True  # Override in subclasses for specific checks

    @abstractmethod
    def detect(self, frame: Any) -> Optional[Dict[str, Any]]:
        """Perform fault detection on a frame/image.

        Args:
            frame: Input frame/image data (format depends on implementation).

        Returns:
            Optional[Dict[str, Any]]: Detection result with fault information,
                or None if no fault detected. Should contain at least:
                - 'fault_type': str - Type of fault detected
                - 'confidence': float - Detection confidence (0.0-1.0)
                - 'location': Optional[str] - Location of fault
                - 'details': Dict[str, Any] - Additional fault details
        """
        pass

    @abstractmethod
    def get_input_source(self) -> Optional[str]:
        """Get the input source for detection (e.g., RTSP URL, camera ID).

        Returns:
            Optional[str]: Input source identifier or None if not configured.
        """
        pass

    def start(self) -> None:
        """Start the fault detection system in background thread.

        Returns:
            None: This method does not return.
        """
        if not self.enabled:
            logger.info('Fault detection "%s" disabled or not configured, skip start', self.name)
            return

        with self._lock:
            if self._thread and self._thread.is_alive():
                logger.warning('Fault detection "%s" already running', self.name)
                return

            self._stop_event.clear()
            self._is_running = True
            self._thread = threading.Thread(
                target=self._run_loop,
                name=f'fault-detection-{self.name}',
                daemon=True,
            )
            self._thread.start()
            logger.info('Fault detection "%s" started', self.name)

    def stop(self, timeout: float = 5.0) -> None:
        """Stop the fault detection system.

        Args:
            timeout (float): Maximum seconds to wait for thread to stop.
        """
        with self._lock:
            self._stop_event.set()
            self._is_running = False
            thread = self._thread
            self._thread = None

        if thread and thread.is_alive():
            thread.join(timeout=timeout)
        logger.info('Fault detection "%s" stopped', self.name)

    def _run_loop(self) -> None:
        """Main detection loop (to be implemented by subclasses if needed).

        This method can be overridden for continuous monitoring scenarios.
        """
        logger.info('Fault detection "%s" run loop started', self.name)
        while not self._stop_event.is_set():
            self._wait_with_stop(1.0)
        logger.info('Fault detection "%s" run loop stopped', self.name)

    def _wait_with_stop(self, duration: float) -> None:
        """Sleep with early exit when detection is stopping.

        Args:
            duration (float): Seconds to wait before returning.
        """
        end_time = time.time() + duration
        while not self._stop_event.is_set() and time.time() < end_time:
            time.sleep(0.2)

    def publish_alarm(
        self,
        fault_type: str,
        confidence: float,
        location: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        image_data: Optional[str] = None,
    ) -> bool:
        """Publish fault detection alarm via MQTT.

        Args:
            fault_type (str): Type of fault detected.
            confidence (float): Detection confidence (0.0-1.0).
            location (Optional[str]): Location identifier.
            details (Optional[Dict[str, Any]]): Additional fault details.
            image_data (Optional[str]): Base64 encoded image if available.

        Returns:
            bool: True if alarm was published successfully.
        """
        if self.mqtt_manager is None:
            logger.debug('Alarm publish skipped: mqtt_manager is None')
            return False

        try:
            from app.services.alarm_publisher import build_alarm_event, publish_alarm_event

            timestamp = datetime.now()
            device_id = f'fault_detection/{self.name}'
            safe_location = location or 'unknown'

            payload = {
                'fault_type': fault_type,
                'confidence': confidence,
                'detection_system': self.name,
                'details': details or {},
            }
            if image_data:
                payload['image_data'] = image_data

            event = build_alarm_event(
                source=f'fault_detection_{self.name}',
                timestamp=timestamp,
                device_id=device_id,
                location=safe_location,
                payload=payload,
            )

            return publish_alarm_event(self.mqtt_manager, event)
        except Exception as exc:
            logger.exception('Failed to publish alarm for "%s": %s', self.name, exc)
            return False

    def is_running(self) -> bool:
        """Check if detection system is currently running.

        Returns:
            bool: True if detection thread is alive.
        """
        with self._lock:
            return self._is_running and self._thread is not None and self._thread.is_alive()

