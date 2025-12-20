"""Conveyor belt fault detection for detecting stones and objects tearing the belt."""

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
    """Attach a dedicated console handler for belt fault detection logs."""
    marker = '_belt_fault_console_handler'
    for handler in logger.handlers:
        if getattr(handler, marker, False):
            return
    console_handler = logging.StreamHandler(stream=sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter('[BELT] %(asctime)s %(levelname)s: %(message)s'))
    setattr(console_handler, marker, True)
    logger.addHandler(console_handler)


_ensure_console_logging()


class ConveyorBeltFaultDetection(FaultDetection):
    """Fault detection system for detecting objects (stones, debris) that may tear the conveyor belt.

    Uses computer vision to detect abnormal objects on the belt surface.
    """

    def __init__(
        self,
        settings: "Settings",
        mqtt_manager: Optional["MQTTManager"] = None,
    ):
        """Initialize conveyor belt fault detection system.

        Args:
            settings (Settings): Application settings (required).
            mqtt_manager (Optional[MQTTManager]): MQTT manager for publishing alarms.
        """
        super().__init__('conveyor_belt_fault', settings, mqtt_manager)
        self._settings = settings
        self._cap: Optional[Any] = None
        self._last_detection_time: float = 0.0
        self._background_model: Optional[Any] = None
        logger.info('Conveyor belt fault detection initialized')

    @property
    def enabled(self) -> bool:
        """Check whether belt fault detection can be started."""
        if cv2 is None:
            return False
        # Use yolo_rtsp_input as fallback, or add belt_fault_rtsp_input to settings
        rtsp_input = getattr(self._settings, 'belt_fault_rtsp_input', None) or getattr(
            self._settings, 'yolo_rtsp_input', ''
        )
        return bool(rtsp_input and cv2 is not None)

    def get_input_source(self) -> Optional[str]:
        """Get RTSP input source."""
        return getattr(self._settings, 'belt_fault_rtsp_input', None) or getattr(
            self._settings, 'yolo_rtsp_input', ''
        ) or None

    def detect(self, frame: Any) -> Optional[Dict[str, Any]]:
        """Detect objects that may damage the conveyor belt.

        Args:
            frame: OpenCV frame (numpy array) or image data.

        Returns:
            Optional[Dict[str, Any]]: Detection result if threat detected, None otherwise.
        """
        if cv2 is None or np is None:
            logger.warning('OpenCV or NumPy not available for belt fault detection')
            return None

        try:
            # Convert to numpy array if needed
            if not isinstance(frame, np.ndarray):
                if hasattr(frame, 'numpy'):
                    frame = frame.numpy()
                else:
                    logger.warning('Unsupported frame type for belt fault detection')
                    return None

            # Detect objects using background subtraction and contour analysis
            detection_result = self._detect_objects_on_belt(frame)

            confidence_threshold = getattr(self._settings, 'belt_fault_confidence_threshold', 0.5)
            if detection_result and detection_result['confidence'] >= confidence_threshold:
                return detection_result

            return None
        except Exception as exc:
            logger.exception('Belt fault detection failed: %s', exc)
            return None

    def _detect_objects_on_belt(self, frame: np.ndarray) -> Optional[Dict[str, Any]]:
        """Detect objects on conveyor belt using background subtraction and contour analysis.

        Args:
            frame: Input frame as numpy array.

        Returns:
            Optional[Dict[str, Any]]: Detection result with confidence and details.
        """
        if np is None or cv2 is None:
            return None

        try:
            # Initialize background subtractor if not already done
            if self._background_model is None:
                self._background_model = cv2.createBackgroundSubtractorMOG2(
                    history=500,
                    varThreshold=50,
                    detectShadows=True,
                )

            # Apply background subtraction
            fg_mask = self._background_model.apply(frame)

            # Morphological operations to clean up the mask
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
            fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_CLOSE, kernel)
            fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_OPEN, kernel)

            # Find contours of detected objects
            contours, _ = cv2.findContours(fg_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            # Filter contours by size and analyze
            size_threshold = getattr(self._settings, 'belt_fault_size_threshold', 50)
            threat_objects = []
            for contour in contours:
                area = cv2.contourArea(contour)
                if area < size_threshold:
                    continue

                # Get bounding box
                x, y, w, h = cv2.boundingRect(contour)
                aspect_ratio = float(w) / h if h > 0 else 0

                # Calculate object characteristics
                # Large, sharp objects (stones, metal) are more dangerous
                perimeter = cv2.arcLength(contour, True)
                circularity = 4 * np.pi * area / (perimeter * perimeter) if perimeter > 0 else 0

                # Objects with low circularity (sharp/irregular) are more likely to be threats
                # Large objects are also threats
                threat_score = 0.0
                if area > size_threshold * 2:
                    threat_score += 0.4  # Large objects
                if circularity < 0.5:
                    threat_score += 0.3  # Irregular/sharp objects
                if aspect_ratio > 2.0 or aspect_ratio < 0.5:
                    threat_score += 0.2  # Elongated objects

                # Calculate confidence based on threat score and size
                confidence = min(1.0, threat_score + (area / (frame.shape[0] * frame.shape[1])) * 2.0)

                if confidence >= 0.3:  # Minimum threshold
                    threat_objects.append({
                        'bbox': (x, y, w, h),
                        'area': int(area),
                        'circularity': float(circularity),
                        'aspect_ratio': float(aspect_ratio),
                        'confidence': float(confidence),
                    })

            if threat_objects:
                # Get the most threatening object
                primary_threat = max(threat_objects, key=lambda obj: obj['confidence'])
                all_areas = [obj['area'] for obj in threat_objects]

                # Overall confidence increases with number and size of threats
                overall_confidence = min(1.0, primary_threat['confidence'] + len(threat_objects) * 0.1)

                x, y, w, h = primary_threat['bbox']
                location_info = f'bbox({x},{y},{w},{h})'

                return {
                    'fault_type': 'object_on_belt',
                    'confidence': float(overall_confidence),
                    'location': location_info,
                    'details': {
                        'threat_count': len(threat_objects),
                        'primary_threat': {
                            'area': primary_threat['area'],
                            'circularity': primary_threat['circularity'],
                            'aspect_ratio': primary_threat['aspect_ratio'],
                        },
                        'all_areas': all_areas,
                        'detection_method': 'background_subtraction',
                    },
                }

            return None
        except Exception as exc:
            logger.exception('Object detection on belt failed: %s', exc)
            return None

    def _run_loop(self) -> None:
        """Run continuous belt fault detection on RTSP stream."""
        if cv2 is None:
            logger.error('OpenCV not available for belt fault detection')
            return

        input_url = self.get_input_source()
        if not input_url:
            logger.error('No RTSP input configured for belt fault detection')
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

            logger.info('Belt fault detection connected to RTSP stream %s', input_url)
            try:
                self._process_stream()
            finally:
                if self._cap:
                    self._cap.release()
                    self._cap = None
                logger.info('Belt fault detection disconnected, will retry shortly')

            self._wait_with_stop(2.0)

    def _process_stream(self) -> None:
        """Process video stream frames for belt fault detection."""
        if self._cap is None or cv2 is None:
            return

        while not self._stop_event.is_set():
            ok, frame = self._cap.read()
            if not ok:
                logger.warning('Failed to read frame from RTSP, waiting before retry')
                self._wait_with_stop(1.0)
                continue

            current_time = time.time()
            detection_interval = getattr(self._settings, 'belt_fault_detection_interval', 1.0)
            if current_time - self._last_detection_time < detection_interval:
                continue

            detection_result = self.detect(frame)
            if detection_result:
                self._last_detection_time = current_time
                logger.warning(
                    'Belt threat detected! Confidence: %.2f, Location: %s, Threats: %d',
                    detection_result['confidence'],
                    detection_result.get('location', 'unknown'),
                    detection_result.get('details', {}).get('threat_count', 0),
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
        """Stop belt fault detection and release resources."""
        if self._cap:
            self._cap.release()
            self._cap = None
        if self._background_model:
            self._background_model = None
        super().stop(timeout)

