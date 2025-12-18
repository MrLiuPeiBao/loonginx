"""YOLOv8 RTSP 行人识别与截图推送服务."""

from __future__ import annotations

import base64
import json
import logging
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
import sys

try:  # pragma: no cover - optional heavy dependency
    import cv2
except ImportError:  # pragma: no cover - optional heavy dependency
    cv2 = None  # type: ignore[assignment]

try:  # pragma: no cover - optional heavy dependency
    from ultralytics import YOLO
except ImportError:  # pragma: no cover - optional heavy dependency
    YOLO = None  # type: ignore[assignment]

try:  # pragma: no cover - optional heavy dependency
    import torch
except ImportError:  # pragma: no cover - optional heavy dependency
    torch = None  # type: ignore[assignment]

from app.core.config import Settings
from app.core.constants import MQTT_TOPICS
from app.db.models import ImageData
from app.db.session import session_scope
from app.mqtt import MQTTManager
from app.services.alarm_publisher import build_alarm_event, publish_alarm_event
from app.services.data_service import DataService

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


def _ensure_console_logging() -> None:
    """Attach a dedicated console handler for YOLO logs."""
    marker = '_yolo_console_handler'
    for handler in logger.handlers:
        if getattr(handler, marker, False):
            return
    console_handler = logging.StreamHandler(stream=sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter('[YOLO] %(asctime)s %(levelname)s: %(message)s'))
    setattr(console_handler, marker, True)
    logger.addHandler(console_handler)


_ensure_console_logging()


class YOLOStreamService:
    """Stream RTSP frames, run YOLO detection, and publish person snapshots."""

    def __init__(self, settings: Settings, mqtt_manager: MQTTManager):
        """Initialize service with runtime dependencies.

        Args:
            settings (Settings): Global configuration loaded from .env.
            mqtt_manager (MQTTManager): Shared MQTT client for publishing.
        """
        self._settings = settings
        self._mqtt = mqtt_manager
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._ffmpeg_process: Optional[subprocess.Popen[bytes]] = None
        self._model: Optional[YOLO] = None  # type: ignore[type-arg]
        self._lock = threading.Lock()
        self._device = 'cuda' if torch is not None and torch.cuda.is_available() else 'cpu'
        self._track_last_capture: Dict[int, float] = {}
        logger.info('YOLO service initialized on device %s', self._device.upper())

    @property
    def enabled(self) -> bool:
        """Check whether background worker can be started.

        Returns:
            bool: True if YOLO is enabled and RTSP input is configured.
        """
        return bool(self._settings.yolo_enabled and self._settings.yolo_rtsp_input)

    def start(self) -> None:
        """Spawn background worker when dependencies and config allow.

        Returns:
            None: This method does not return.
        """
        if not self.enabled:
            logger.info('YOLO service disabled or missing RTSP input, skip start')
            return
        if cv2 is None or YOLO is None:
            logger.warning('YOLO service disabled, dependencies unavailable (cv2=%s, YOLO=%s)', bool(cv2), bool(YOLO))
            return
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                name='yolo-stream',
                daemon=True,
            )
            self._thread.start()
            logger.info('YOLO stream service started')

    def stop(self, timeout: float = 5.0) -> None:
        """Signal worker to stop and wait for graceful exit.

        Args:
            timeout (float, optional): Maximum seconds to wait for join.
        """
        with self._lock:
            self._stop_event.set()
            thread = self._thread
            self._thread = None
        if thread and thread.is_alive():
            thread.join(timeout=timeout)
        self._terminate_ffmpeg()
        logger.info('YOLO stream service stopped')

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _run_loop(self) -> None:
        """Run RTSP capture loop with automatic reconnects."""
        assert cv2 is not None  # for type-checkers
        assert YOLO is not None
        try:
            logger.info('Loading YOLO weights from %s', self._settings.yolo_model_path)
            self._model = self._model or self._load_model()
            logger.info('YOLO model ready on %s', self._device.upper())
        except Exception as exc:  # pragma: no cover - hardware dependency
            logger.exception('Failed to load YOLO model: %s', exc)
            return

        input_url = self._settings.yolo_rtsp_input
        reconnect_delay = 5.0
        while not self._stop_event.is_set():
            cap = cv2.VideoCapture(input_url, getattr(cv2, 'CAP_FFMPEG', 0))
            if not cap.isOpened():
                logger.error('Unable to open RTSP stream %s, retry in %.1fs', input_url, reconnect_delay)
                cap.release()
                self._wait_with_stop(reconnect_delay)
                continue

            logger.info('YOLO stream connected to %s', input_url)
            try:
                self._start_ffmpeg()
                self._process_stream(cap)
            finally:
                cap.release()
                self._terminate_ffmpeg()
                logger.info('YOLO stream disconnected, will retry shortly')

            self._wait_with_stop(2.0)

    def _process_stream(self, cap: 'cv2.VideoCapture') -> None:
        """Track persons, push snapshots, and forward annotated frames."""
        assert cv2 is not None
        if self._model is None:
            return

        detection_interval = max(
            1.0,
            float(
                self._settings.yolo_detection_interval
                or self._settings.yolo_screenshot_interval
                or 5.0
            ),
        )
        conf_threshold = max(
            0.05,
            min(
                0.95,
                float(
                    self._settings.yolo_person_confidence
                    or self._settings.yolo_confidence_threshold
                    or 0.35
                ),
            ),
        )
        tracker_config = self._settings.yolo_tracker_config or 'bytetrack.yaml'

        while not self._stop_event.is_set():
            ok, frame = cap.read()
            if not ok:
                logger.warning('Failed to read frame from RTSP, waiting before retry')
                self._wait_with_stop(1.0)
                continue

            frame_start = time.time()
            try:
                results_list = self._model.track(
                    frame,
                    persist=True,
                    verbose=False,
                    device=self._device,
                    classes=0,
                    conf=conf_threshold,
                    tracker=tracker_config,
                )
                results = results_list[0]
            except Exception as exc:  # pragma: no cover - GPU specific
                logger.exception('YOLO tracking failed: %s', exc)
                self._wait_with_stop(1.0)
                continue

            annotated_frame = results.plot()
            self._handle_tracks(frame, annotated_frame, results, detection_interval)
            self._write_ffmpeg_frame(annotated_frame)

            elapsed_ms = (time.time() - frame_start) * 1000.0
            logger.info('YOLO frame processed in %.2f ms', elapsed_ms)

    def _handle_tracks(
        self,
        raw_frame: Any,
        annotated_frame: Any,
        results: Any,
        detection_interval: float,
    ) -> None:
        """Emit snapshots for tracked persons with per-track cooldown."""
        boxes = getattr(results, 'boxes', None)
        ids = getattr(boxes, 'id', None) if boxes is not None else None
        if boxes is None or ids is None:
            return

        now = time.time()
        for box in boxes:
            track_identifier = getattr(box, 'id', None)
            if track_identifier is None:
                continue
            try:
                track_id = int(
                    track_identifier.item()
                    if hasattr(track_identifier, 'item')
                    else track_identifier
                )
            except Exception:
                continue

            last_capture = self._track_last_capture.get(track_id, 0.0)
            if now - last_capture < detection_interval:
                continue

            self._track_last_capture[track_id] = now
            timestamp = datetime.now()
            coords = getattr(box, 'xyxy', None)
            logger.info(
                'Track %s captured at %s (cooldown %.1fs remaining)',
                track_id,
                timestamp.isoformat(),
                detection_interval,
            )
            if coords is not None:
                try:
                    bbox = coords.tolist()[0]
                    logger.debug('Track %s bbox: %s', track_id, bbox)
                except Exception:
                    logger.debug('Track %s bbox unavailable', track_id)
            self._emit_snapshots(track_id, raw_frame, annotated_frame, timestamp)

    def _emit_snapshots(
        self,
        track_id: int,
        raw_frame: Any,
        annotated_frame: Any,
        timestamp: datetime,
    ) -> None:
        """Publish both raw and annotated snapshots."""
        assert cv2 is not None
        device_id = self._settings.yolo_device_id or 'rtsp-camera'
        location = self._resolve_location()
        unix_time = timestamp.timestamp()
        image_name = f'person_{track_id}_{timestamp.strftime("%Y%m%d_%H%M%S_%f")}.jpg'

        raw_b64 = self._encode_frame(raw_frame, quality=80)
        if raw_b64:
            payload = {
                'track_id': track_id,
                'timestamp': timestamp.isoformat(),
                'ts': unix_time,
                'device_id': device_id,
                'location': location,
                'image_name': image_name,
                'img': raw_b64,
            }
            logger.info('Publishing raw snapshot for track %s (%s)', track_id, image_name)
            self._publish_payload(MQTT_TOPICS['person_image'], payload, 'raw snapshot')
            self._store_snapshot(timestamp, device_id, location, image_name, raw_b64)
            publish_alarm_event(
                self._mqtt,
                build_alarm_event(
                    source='yolo_person',
                    timestamp=timestamp,
                    device_id=device_id,
                    location=location,
                    payload={
                        'track_id': int(track_id),
                        'image_name': image_name,
                        'topic': MQTT_TOPICS['person_image'],
                    },
                ),
            )
        else:
            logger.error('Failed to encode raw snapshot for track %s', track_id)

        annotated_b64 = self._encode_frame(annotated_frame, quality=85)
        if annotated_b64:
            payload = {
                'track_id': track_id,
                'timestamp': timestamp.isoformat(),
                'ts': unix_time,
                'device_id': device_id,
                'location': location,
                'img': annotated_b64,
            }
            logger.info('Publishing annotated snapshot for track %s', track_id)
            self._publish_payload(
                MQTT_TOPICS['annotated_person_image'],
                payload,
                'annotated snapshot',
            )
        else:
            logger.error('Failed to encode annotated snapshot for track %s', track_id)

    def _resolve_location(self) -> str:
        """Resolve current location (prefer latest RFID card_id, fallback to config)."""
        fallback = self._settings.yolo_location or 'rtsp'
        try:
            with session_scope() as session:
                service = DataService(session)
                latest_rfid = service.get_latest_rfid_card()
                if latest_rfid:
                    return latest_rfid
        except Exception:  # pragma: no cover - best-effort
            logger.debug('Failed to resolve latest RFID for YOLO snapshot location')
        return fallback

    def _encode_frame(self, frame: Any, *, quality: int) -> Optional[str]:
        """Encode frame into JPEG base64 string."""
        assert cv2 is not None
        try:
            ok, buffer = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
        except Exception as exc:  # pragma: no cover - codec specific
            logger.error('JPEG encoding failed: %s', exc)
            return None
        if not ok:
            return None
        return base64.b64encode(buffer).decode('ascii')

    def _publish_payload(self, topic: str, payload: Dict[str, Any], description: str) -> None:
        """Publish payload to MQTT with logging."""
        try:
            payload_bytes = json.dumps(payload).encode('utf-8')
            logger.debug('Publishing %s (%d bytes) to %s', description, len(payload_bytes), topic)
            if not self._mqtt.publish(topic, payload=payload_bytes, qos=1):
                logger.warning('Failed to publish %s to %s', description, topic)
            else:
                logger.info('Published %s to topic %s', description, topic)
        except Exception:  # pragma: no cover - MQTT dependency
            logger.exception('Failed to publish %s', description)

    def _store_snapshot(
        self,
        timestamp: datetime,
        device_id: str,
        location: str,
        image_name: str,
        image_data: str,
    ) -> None:
        """Persist snapshot metadata into database."""
        try:
            with session_scope() as session:
                service = DataService(session)
                entity = ImageData(
                    timestamp=timestamp,
                    device_id=device_id,
                    image_name=image_name,
                    image_data=image_data,
                    location=location,
                )
                service.create_image_data([entity])
            logger.info(
                'Snapshot stored in database: name=%s device=%s location=%s',
                image_name,
                device_id,
                location,
            )
        except Exception:  # pragma: no cover - database dependency
            logger.exception('Failed to store snapshot %s', image_name)

    def _write_ffmpeg_frame(self, annotated_frame: Any) -> None:
        """Forward annotated frames through FFmpeg for RTSP restream."""
        if self._ffmpeg_process is None or self._ffmpeg_process.poll() is not None:
            return
        if cv2 is None:
            return
        width = int(self._settings.yolo_frame_width or annotated_frame.shape[1])
        height = int(self._settings.yolo_frame_height or annotated_frame.shape[0])
        resized = cv2.resize(annotated_frame, (width, height))
        try:
            self._ffmpeg_process.stdin.write(resized.tobytes())
        except Exception as exc:  # pragma: no cover - process specific
            logger.warning('Failed to write frame to FFmpeg: %s', exc)
            self._terminate_ffmpeg()

    def _start_ffmpeg(self) -> None:
        """Launch FFmpeg process for optional RTSP restreaming."""
        if not self._settings.yolo_ffmpeg_enabled:
            return
        output_url = self._settings.yolo_rtsp_output
        if not output_url:
            return
        if self._ffmpeg_process and self._ffmpeg_process.poll() is None:
            return

        frame_width = int(self._settings.yolo_frame_width or 640)
        frame_height = int(self._settings.yolo_frame_height or 360)
        fps = int(self._settings.yolo_fps or 25)

        cmd = [
            'ffmpeg',
            '-loglevel',
            'error',
            '-f',
            'rawvideo',
            '-pixel_format',
            'bgr24',
            '-video_size',
            f'{frame_width}x{frame_height}',
            '-framerate',
            str(fps),
            '-i',
            '-',
            '-c:v',
            'libx264',
            '-preset',
            'ultrafast',
            '-tune',
            'zerolatency',
            '-pix_fmt',
            'yuv420p',
            '-f',
            'rtsp',
            '-rtsp_transport',
            'tcp',
            output_url,
        ]

        try:
            logger.info('Starting FFmpeg restream to %s (%sx%s@%sfps)', output_url, frame_width, frame_height, fps)
            self._ffmpeg_process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            logger.info('FFmpeg started for RTSP output %s', output_url)
        except FileNotFoundError:
            logger.warning('FFmpeg executable not found, disable RTSP restreaming')
            self._ffmpeg_process = None
        except Exception as exc:  # pragma: no cover - ffmpeg specific
            logger.exception('Failed to start FFmpeg: %s', exc)
            self._ffmpeg_process = None

    def _terminate_ffmpeg(self) -> None:
        """Stop FFmpeg subprocess if running."""
        proc = self._ffmpeg_process
        self._ffmpeg_process = None
        if proc is None:
            return
        logger.info('Stopping FFmpeg restream process')
        try:
            stdin = proc.stdin
            if stdin:
                stdin.close()
            proc.terminate()
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:  # pragma: no cover - ffmpeg specific
            proc.kill()
        except Exception as exc:  # pragma: no cover - ffmpeg specific
            logger.debug('FFmpeg termination raised %s', exc)

    def _load_model(self) -> YOLO:
        """Load YOLO model weights from configured path.

        Returns:
            YOLO: Loaded Ultralytics model instance.
        """
        weights = Path(self._settings.yolo_model_path or 'yolov8n.pt')
        if not weights.is_file():
            logger.warning('YOLO weights %s not found, attempting download', weights)
        return YOLO(str(weights))  # type: ignore[return-value]

    def _wait_with_stop(self, duration: float) -> None:
        """Sleep with early exit when service is stopping.

        Args:
            duration (float): Seconds to wait before returning.
        """
        end_time = time.time() + duration
        while not self._stop_event.is_set() and time.time() < end_time:
            time.sleep(0.2)
