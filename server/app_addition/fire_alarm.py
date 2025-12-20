"""Fire detection system for coal moving on conveyor belt."""

from __future__ import annotations

import base64
import logging
import sys
import time
from typing import Any, Dict, Optional, TYPE_CHECKING

try:
    import cv2
except ImportError:
    cv2 = None  # type: ignore[assignment]

try:
    import numpy as np
except ImportError:
    np = None  # type: ignore[assignment]

from .fault_dection import FaultDetection

if TYPE_CHECKING:
    from app.mqtt import MQTTManager
    from app.core.config import Settings

logger = logging.getLogger(__name__)


def _ensure_console_logging() -> None:
    """Attach a dedicated console handler for fire detection logs."""
    marker = '_fire_alarm_console_handler'
    for handler in logger.handlers:
        if getattr(handler, marker, False):
            return
    console_handler = logging.StreamHandler(stream=sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter('[FIRE] %(asctime)s %(levelname)s: %(message)s'))
    setattr(console_handler, marker, True)
    logger.addHandler(console_handler)


_ensure_console_logging()


class FireAlarm(FaultDetection):
    """Fire detection system for detecting fire threats in coal on conveyor belt.

    Uses computer vision techniques to detect flames and smoke in video streams.
    """

    def __init__(
        self,
        settings: "Settings",
        mqtt_manager: Optional["MQTTManager"] = None,
    ):
        """Initialize fire detection system.

        Args:
            settings (Settings): Application settings (required).
            mqtt_manager (Optional[MQTTManager]): MQTT manager for publishing alarms.
        """
        super().__init__('fire_alarm', settings, mqtt_manager)
        self._settings = settings
        self._cap: Optional[Any] = None
        self._last_detection_time: float = 0.0
        logger.info('Fire alarm detection initialized')

    @property
    def enabled(self) -> bool:
        """Check whether fire detection can be started."""
        if cv2 is None:
            return False
        # Use yolo_rtsp_input as fallback, or add fire_alarm_rtsp_input to settings
        rtsp_input = getattr(self._settings, 'fire_alarm_rtsp_input', None) or getattr(
            self._settings, 'yolo_rtsp_input', ''
        )
        return bool(rtsp_input and cv2 is not None)

    def get_input_source(self) -> Optional[str]:
        """Get RTSP input source."""
        return getattr(self._settings, 'fire_alarm_rtsp_input', None) or getattr(
            self._settings, 'yolo_rtsp_input', ''
        ) or None

    def detect(self, frame: Any) -> Optional[Dict[str, Any]]:
        """Detect fire in the given frame.

        Args:
            frame: OpenCV frame (numpy array) or image data.

        Returns:
            Optional[Dict[str, Any]]: Detection result if fire detected, None otherwise.
        """
        if cv2 is None or np is None:
            logger.warning('OpenCV or NumPy not available for fire detection')
            return None

        try:
            # Convert to numpy array if needed
            if not isinstance(frame, np.ndarray):
                if hasattr(frame, 'numpy'):
                    frame = frame.numpy()
                else:
                    logger.warning('Unsupported frame type for fire detection')
                    return None

            # Fire detection using color-based analysis
            # Fire typically has high red/orange/yellow intensity
            detection_result = self._detect_fire_by_color(frame)

            confidence_threshold = getattr(self._settings, 'fire_alarm_confidence_threshold', 0.6)
            if detection_result and detection_result['confidence'] >= confidence_threshold:
                return detection_result

            return None
        except Exception as exc:
            logger.exception('Fire detection failed: %s', exc)
            return None

    def _detect_fire_by_color(self, frame: np.ndarray) -> Optional[Dict[str, Any]]:
        """Detect fire using color-based analysis.

        Fire typically appears as bright orange/red/yellow regions with high intensity.

        Args:
            frame: Input frame as numpy array.

        Returns:
            Optional[Dict[str, Any]]: Detection result with confidence and details.
        """
        if np is None:
            return None

        try:
            # Convert BGR to HSV for better color analysis
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

            # Define color ranges for fire (orange/red/yellow)
            # Lower bound for fire colors (orange-red range)
            lower_fire1 = np.array([0, 120, 200])  # Red-orange
            upper_fire1 = np.array([10, 255, 255])

            # Upper bound for fire colors (yellow range)
            lower_fire2 = np.array([20, 100, 200])  # Yellow-orange
            upper_fire2 = np.array([30, 255, 255])

            # Create masks for fire colors
            mask1 = cv2.inRange(hsv, lower_fire1, upper_fire1)
            mask2 = cv2.inRange(hsv, lower_fire2, upper_fire2)
            fire_mask = cv2.bitwise_or(mask1, mask2)

            # Calculate fire region statistics
            fire_pixels = np.sum(fire_mask > 0)
            total_pixels = frame.shape[0] * frame.shape[1]
            fire_ratio = fire_pixels / total_pixels if total_pixels > 0 else 0.0

            # Calculate confidence based on fire region size and intensity
            if fire_ratio > 0.01:  # At least 1% of frame shows fire colors
                # Additional check: high brightness in fire regions
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                fire_region_brightness = np.mean(gray[fire_mask > 0]) if fire_pixels > 0 else 0

                # Confidence increases with fire ratio and brightness
                confidence = min(1.0, fire_ratio * 10.0 + (fire_region_brightness / 255.0) * 0.5)

                if confidence >= 0.3:  # Minimum threshold
                    # Find fire region bounding box
                    contours, _ = cv2.findContours(fire_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                    if contours:
                        largest_contour = max(contours, key=cv2.contourArea)
                        x, y, w, h = cv2.boundingRect(largest_contour)
                        location_info = f'bbox({x},{y},{w},{h})'
                    else:
                        location_info = 'unknown'

                    return {
                        'fault_type': 'fire_detected',
                        'confidence': float(confidence),
                        'location': location_info,
                        'details': {
                            'fire_ratio': float(fire_ratio),
                            'fire_pixels': int(fire_pixels),
                            'brightness': float(fire_region_brightness),
                            'detection_method': 'color_based',
                        },
                    }

            return None
        except Exception as exc:
            logger.exception('Color-based fire detection failed: %s', exc)
            return None

    def _run_loop(self) -> None:
        """Run continuous fire detection on RTSP stream."""
        if cv2 is None:
            logger.error('OpenCV not available for fire detection')
            return

        input_url = self.get_input_source()
        if not input_url:
            logger.error('No RTSP input configured for fire detection')
            return

        reconnect_delay = 5.0

        while not self._stop_event.is_set():
            self._cap = cv2.VideoCapture(input_url, getattr(cv2, 'CAP_FFMPEG', 0))
            if not self._cap.isOpened():
                logger.error('Unable to open RTSP stream %s, retry in %.1fs', input_url, reconnect_delay)
                if self._cap:
                    self._cap.release()
                self._wait_with_stop(reconnect_delay)
                continue

            logger.info('Fire detection connected to RTSP stream %s', input_url)
            try:
                self._process_stream()
            finally:
                if self._cap:
                    self._cap.release()
                    self._cap = None
                logger.info('Fire detection disconnected, will retry shortly')

            self._wait_with_stop(2.0)

    def _process_stream(self) -> None:
        """Process video stream frames for fire detection."""
        if self._cap is None or cv2 is None:
            return

        while not self._stop_event.is_set():
            ok, frame = self._cap.read()
            if not ok:
                logger.warning('Failed to read frame from RTSP, waiting before retry')
                self._wait_with_stop(1.0)
                continue

            current_time = time.time()
            detection_interval = getattr(self._settings, 'fire_alarm_detection_interval', 2.0)
            if current_time - self._last_detection_time < detection_interval:
                continue

            detection_result = self.detect(frame)
            if detection_result:
                self._last_detection_time = current_time
                logger.warning(
                    'Fire detected! Confidence: %.2f, Location: %s',
                    detection_result['confidence'],
                    detection_result.get('location', 'unknown'),
                )

                # Encode frame as base64 for alarm
                image_data = self._encode_frame(frame)
                self.publish_alarm(
                    fault_type=detection_result['fault_type'],
                    confidence=detection_result['confidence'],
                    location=detection_result.get('location'),
                    details=detection_result.get('details', {}),
                    image_data=image_data,
                )

    def _encode_frame(self, frame: Any) -> Optional[str]:
        """Encode frame into JPEG base64 string."""
        if cv2 is None:
            return None
        try:
            ok, buffer = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            if not ok:
                return None
            return base64.b64encode(buffer).decode('ascii')
        except Exception as exc:
            logger.error('Frame encoding failed: %s', exc)
            return None

    def stop(self, timeout: float = 5.0) -> None:
        """Stop fire detection and release resources."""
        if self._cap:
            self._cap.release()
            self._cap = None
        super().stop(timeout)

