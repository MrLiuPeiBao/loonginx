"""YOLOv8 RTSP 行人识别与截图推送服务."""

from __future__ import annotations

import base64
import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional
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
from app.ipc import IPCUnavailableError
from app.mqtt import MQTTManager
from app.services.alarm_publisher import build_alarm_event, publish_alarm_event
from app.services.rtsp_capture import (
    RtspCaptureConfig,
    calc_drop_frames,
    drain_capture,
    open_capture,
)
from app.services.rtsp_output import RtspOutput, RtspOutputConfig, create_rtsp_output

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

if TYPE_CHECKING:
    from app.runtime.db_worker import DBWorker
    from app.runtime.telemetry_bridge import TelemetryBridgeClient


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


def _next_rtsp_failure_backoff(
    current_seconds: float,
    *,
    initial_seconds: float,
    max_seconds: float,
) -> float:
    """Return bounded exponential backoff for offline RTSP sources."""
    initial = max(1.0, float(initial_seconds or 1.0))
    maximum = max(initial, float(max_seconds or initial))
    if current_seconds <= 0:
        return initial
    return min(maximum, max(initial, current_seconds * 2.0))


class YOLOStreamService:
    """Stream RTSP frames, run YOLO detection, and publish person snapshots."""

    def __init__(
        self,
        settings: Settings,
        mqtt_manager: Optional[MQTTManager] = None,
        telemetry_client: Optional["TelemetryBridgeClient"] = None,
        db_worker: Optional["DBWorker"] = None,
    ):
        """Initialize service with runtime dependencies.

        Args:
            settings (Settings): Global configuration loaded from .env.
            mqtt_manager (MQTTManager): Shared MQTT client for publishing.
        """
        self._settings = settings
        self._mqtt = mqtt_manager
        self._telemetry = telemetry_client
        self._db_worker = db_worker
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._rtsp_output: Optional[RtspOutput] = None
        self._model: Optional[YOLO] = None  # type: ignore[type-arg]
        self._lock = threading.Lock()
        self._device = self._resolve_compute_device()
        self._track_last_capture: Dict[int, float] = {}
        self._last_telemetry_unavailable_log = 0.0
        self._telemetry_unavailable_suppressed = 0
        self._watchdog_lock = threading.Lock()
        self._watchdog: Dict[str, Any] = {
            'stream_restart_count': 0,
            'consecutive_inference_timeouts': 0,
            'last_inference_started_at': None,
            'last_inference_finished_at': None,
            'last_inference_duration_ms': None,
            'last_watchdog_event': '',
            'last_watchdog_event_at': None,
            'last_stream_open_failed_at': None,
        }
        logger.info('YOLO service initialized on device %s', self._device.upper())

    @property
    def enabled(self) -> bool:
        """Check whether background worker can be started.

        Returns:
            bool: True if YOLO is enabled and RTSP input is configured.
        """
        return bool(self._settings.yolo_enabled and self._settings.resolve_yolo_rtsp_input())

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
        self._stop_rtsp_output()
        logger.info('YOLO stream service stopped')

    def apply_settings(self, settings: Settings) -> None:
        """Apply new settings and restart background worker if needed."""
        self._settings = settings
        # Restart to ensure new RTSP/model/encoding config takes effect
        self.stop()
        self.start()

    def get_watchdog_status(self) -> Dict[str, Any]:
        """Return a thread-safe YOLO watchdog status snapshot."""
        with self._watchdog_lock:
            payload = dict(self._watchdog)
        payload.update(
            {
                'enabled': bool(self._settings.yolo_enabled),
                'run_mode': str(self._settings.yolo_run_mode or 'thread'),
                'inference_timeout_seconds': float(getattr(self._settings, 'yolo_inference_timeout_seconds', 8.0) or 8.0),
                'inference_timeout_consecutive_limit': int(
                    getattr(self._settings, 'yolo_inference_timeout_consecutive_limit', 1) or 1
                ),
                'watchdog_max_stream_restarts': int(
                    getattr(self._settings, 'yolo_watchdog_max_stream_restarts', 3) or 3
                ),
            }
        )
        return payload

    def _watchdog_update(self, **kwargs: Any) -> None:
        with self._watchdog_lock:
            self._watchdog.update(kwargs)

    def _watchdog_mark_event(self, event: str) -> None:
        now = datetime.now().isoformat(timespec='seconds')
        self._watchdog_update(last_watchdog_event=str(event), last_watchdog_event_at=now)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _log_telemetry_unavailable(self, exc: BaseException) -> None:
        """Log missing telemetry bridge without flooding the inference loop."""
        now = time.monotonic()
        if now - self._last_telemetry_unavailable_log < 10.0:
            self._telemetry_unavailable_suppressed += 1
            return

        suppressed = self._telemetry_unavailable_suppressed
        self._telemetry_unavailable_suppressed = 0
        self._last_telemetry_unavailable_log = now
        if suppressed:
            logger.warning(
                'Telemetry bridge unavailable; skipping YOLO snapshot until API pipe is ready: %s '
                '(suppressed=%d)',
                exc,
                suppressed,
            )
        else:
            logger.warning(
                'Telemetry bridge unavailable; skipping YOLO snapshot until API pipe is ready: %s',
                exc,
            )

    def _resolve_compute_device(self) -> str:
        requested = str(getattr(self._settings, 'yolo_compute_device', 'auto') or 'auto').strip()
        normalized = requested.lower()
        cuda_available = bool(torch is not None and torch.cuda.is_available())

        if normalized in {'', 'auto'}:
            return 'cuda:0' if cuda_available else 'cpu'
        if normalized in {'gpu', 'cuda'}:
            if cuda_available:
                return 'cuda:0'
            logger.warning('YOLO_COMPUTE_DEVICE=%s requested but CUDA is unavailable; fallback to CPU', requested)
            return 'cpu'
        if normalized.startswith('cuda') and not cuda_available:
            logger.warning('YOLO_COMPUTE_DEVICE=%s requested but CUDA is unavailable; fallback to CPU', requested)
            return 'cpu'
        return requested

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

        input_url = self._settings.resolve_yolo_rtsp_input()
        reconnect_delay = max(
            1.0,
            float(
                getattr(
                    self._settings,
                    'yolo_rtsp_failure_backoff_initial_seconds',
                    10.0,
                )
                or 10.0
            ),
        )
        max_reconnect_delay = max(
            reconnect_delay,
            float(
                getattr(
                    self._settings,
                    'yolo_rtsp_failure_backoff_max_seconds',
                    120.0,
                )
                or reconnect_delay
            ),
        )
        failed_opens = 0
        stream_restarts = 0
        max_stream_restarts = max(
            0,
            int(getattr(self._settings, 'yolo_watchdog_max_stream_restarts', 3) or 0),
        )
        reconnect_cooldown = max(
            0.0,
            float(getattr(self._settings, 'yolo_watchdog_reconnect_cooldown_seconds', 2.0) or 0.0),
        )
        while not self._stop_event.is_set():
            capture_config = RtspCaptureConfig(
                input_url=input_url,
                buffer_size=max(0, int(self._settings.yolo_capture_buffer_size or 0)),
                capture_options=(self._settings.yolo_rtsp_capture_options or '').strip(),
            )
            cap = open_capture(capture_config)
            if cap is None or not cap.isOpened():
                failed_opens += 1
                logger.error(
                    'Unable to open RTSP stream %s, retry in %.1fs (failures=%d)',
                    input_url,
                    reconnect_delay,
                    failed_opens,
                )
                self._watchdog_update(last_stream_open_failed_at=datetime.now().isoformat(timespec='seconds'))
                self._watchdog_mark_event('stream_open_failed')
                if cap is not None:
                    cap.release()
                self._wait_with_stop(reconnect_delay)
                reconnect_delay = _next_rtsp_failure_backoff(
                    reconnect_delay,
                    initial_seconds=getattr(
                        self._settings,
                        'yolo_rtsp_failure_backoff_initial_seconds',
                        10.0,
                    ),
                    max_seconds=max_reconnect_delay,
                )
                continue

            logger.info('YOLO stream connected to %s', input_url)
            self._watchdog_mark_event('stream_connected')
            reconnect_delay = max(
                1.0,
                float(
                    getattr(
                        self._settings,
                        'yolo_rtsp_failure_backoff_initial_seconds',
                        10.0,
                    )
                    or 10.0
                ),
            )
            failed_opens = 0
            try:
                self._start_rtsp_output()
                break_reason = self._process_stream(cap)
                if break_reason == 'watchdog_timeout':
                    stream_restarts += 1
                    self._watchdog_update(stream_restart_count=stream_restarts)
                    self._watchdog_mark_event('watchdog_timeout_reconnect')
                    if max_stream_restarts > 0 and stream_restarts > max_stream_restarts:
                        logger.error(
                            'YOLO watchdog reached max stream restarts (%d), stopping stream loop',
                            max_stream_restarts,
                        )
                        self._watchdog_mark_event('watchdog_restart_limit_reached')
                        break
            finally:
                cap.release()
                self._stop_rtsp_output()
                logger.info('YOLO stream disconnected, will retry shortly')

            wait_seconds = reconnect_cooldown if reconnect_cooldown > 0 else 2.0
            self._wait_with_stop(wait_seconds)

    def _process_stream(self, cap: 'cv2.VideoCapture') -> str:
        """Track persons, push snapshots, and forward annotated frames."""
        assert cv2 is not None
        if self._model is None:
            return 'model_unavailable'

        snapshot_interval = max(
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
        target_fps = max(1, int(self._settings.yolo_fps or 25))
        target_frame_interval = 1.0 / float(target_fps)
        drop_frames = bool(self._settings.yolo_drop_frames)
        max_drop_frames = int(self._settings.yolo_drop_max_frames or target_fps)
        if max_drop_frames < 0:
            max_drop_frames = 0
        read_failure_reconnect_threshold = max(
            1,
            int(getattr(self._settings, 'yolo_read_failure_reconnect_threshold', 5) or 5),
        )
        inference_interval = max(
            0.05,
            float(getattr(self._settings, 'yolo_inference_interval', 0.2) or 0.2),
        )
        inference_size = max(
            320,
            int(getattr(self._settings, 'yolo_inference_size', 640) or 640),
        )
        last_inference_started = 0.0
        latest_annotated_frame: Optional[Any] = None
        inference_lock = threading.Lock()
        inference_busy = threading.Event()
        inference_busy_since = {'value': 0.0}
        forwarded_frames = 0
        inference_runs = 0
        consecutive_read_failures = 0
        consecutive_inference_timeouts = 0
        inference_timeout_seconds = max(
            0.5,
            float(getattr(self._settings, 'yolo_inference_timeout_seconds', 8.0) or 8.0),
        )
        inference_timeout_limit = max(
            1,
            int(getattr(self._settings, 'yolo_inference_timeout_consecutive_limit', 1) or 1),
        )
        last_stream_log = time.time()

        def run_inference(raw_frame: Any) -> None:
            nonlocal latest_annotated_frame
            inference_start = time.time()
            self._watchdog_update(last_inference_started_at=datetime.now().isoformat(timespec='seconds'))
            try:
                results_list = self._model.track(
                    raw_frame,
                    persist=True,
                    verbose=False,
                    device=self._device,
                    classes=0,
                    conf=conf_threshold,
                    tracker=tracker_config,
                    imgsz=inference_size,
                )
                results = results_list[0]
                boxes = getattr(results, 'boxes', None)
                box_count = len(boxes) if boxes is not None else 0
                logger.info(
                    'YOLO inference found %d person boxes at confidence %.2f',
                    box_count,
                    conf_threshold,
                )
                annotated_frame = results.plot()
                self._handle_tracks(raw_frame, annotated_frame, results, snapshot_interval)
                with inference_lock:
                    latest_annotated_frame = annotated_frame
                elapsed_ms = (time.time() - inference_start) * 1000.0
                logger.debug('YOLO inference processed in %.2f ms', elapsed_ms)
                self._watchdog_update(
                    last_inference_duration_ms=float(elapsed_ms),
                    last_inference_finished_at=datetime.now().isoformat(timespec='seconds'),
                )
            except Exception as exc:  # pragma: no cover - GPU specific
                logger.exception('YOLO tracking failed: %s', exc)
                self._watchdog_mark_event('inference_error')
            finally:
                inference_busy.clear()
                inference_busy_since['value'] = 0.0

        while not self._stop_event.is_set():
            ok, frame = cap.read()
            if not ok:
                consecutive_read_failures += 1
                logger.warning(
                    'Failed to read frame from RTSP (%d/%d), waiting before retry',
                    consecutive_read_failures,
                    read_failure_reconnect_threshold,
                )
                if consecutive_read_failures >= read_failure_reconnect_threshold:
                    logger.warning(
                        'RTSP read failed %d times consecutively; reopening stream',
                        consecutive_read_failures,
                    )
                    self._watchdog_mark_event('rtsp_read_failure_reconnect')
                    return 'read_failures'
                self._wait_with_stop(1.0)
                continue
            consecutive_read_failures = 0

            frame_start = time.time()
            now = time.time()
            if (
                not inference_busy.is_set()
                and now - last_inference_started >= inference_interval
            ):
                last_inference_started = now
                inference_runs += 1
                inference_busy.set()
                inference_busy_since['value'] = now
                infer_frame = frame.copy()
                threading.Thread(
                    target=run_inference,
                    args=(infer_frame,),
                    name='yolo-inference',
                    daemon=True,
                ).start()
            elif inference_busy.is_set():
                busy_for = now - float(inference_busy_since['value'] or now)
                if busy_for >= inference_timeout_seconds:
                    consecutive_inference_timeouts += 1
                    self._watchdog_update(
                        consecutive_inference_timeouts=consecutive_inference_timeouts,
                    )
                    logger.error(
                        'YOLO inference watchdog timeout busy_for=%.2fs threshold=%.2fs consecutive=%d/%d',
                        busy_for,
                        inference_timeout_seconds,
                        consecutive_inference_timeouts,
                        inference_timeout_limit,
                    )
                    self._watchdog_mark_event('inference_timeout')
                    inference_busy.clear()
                    inference_busy_since['value'] = 0.0
                    last_inference_started = now
                    if consecutive_inference_timeouts >= inference_timeout_limit:
                        logger.error(
                            'YOLO watchdog triggered stream reconnect due to consecutive inference timeouts=%d',
                            consecutive_inference_timeouts,
                        )
                        self._watchdog_mark_event('watchdog_timeout_break_stream')
                        return 'watchdog_timeout'

            with inference_lock:
                output_frame = latest_annotated_frame
                latest_annotated_frame = None
            if output_frame is None:
                output_frame = frame
            else:
                output_frame = output_frame.copy()
                consecutive_inference_timeouts = 0
                self._watchdog_update(consecutive_inference_timeouts=0)
            self._write_rtsp_frame(output_frame)
            forwarded_frames += 1

            elapsed_ms = (time.time() - frame_start) * 1000.0
            if drop_frames:
                drop_count = calc_drop_frames(elapsed_ms / 1000.0, target_fps, max_drop_frames)
                if drop_count:
                    drained = drain_capture(cap, drop_count)
                    if drained:
                        logger.debug('Dropped %d frames to reduce lag', drained)
            now = time.time()
            if now - last_stream_log >= 5.0:
                logger.info(
                    'YOLO stream forwarded %d frames, scheduled %d inference jobs in %.1fs',
                    forwarded_frames,
                    inference_runs,
                    now - last_stream_log,
                )
                forwarded_frames = 0
                inference_runs = 0
                last_stream_log = now
            sleep_seconds = target_frame_interval - (time.time() - frame_start)
            if sleep_seconds > 0:
                self._stop_event.wait(sleep_seconds)
            logger.debug('YOLO stream frame forwarded in %.2f ms', elapsed_ms)
        return 'stop_event'

    def _handle_tracks(
        self,
        raw_frame: Any,
        annotated_frame: Any,
        results: Any,
        detection_interval: float,
    ) -> None:
        """Emit snapshots for tracked persons with per-track cooldown."""
        boxes = getattr(results, 'boxes', None)
        if boxes is None:
            return

        now = time.time()
        for index, box in enumerate(boxes):
            track_id = self._resolve_track_id(box, index)
            if track_id is None:
                logger.debug('Skip YOLO box without usable track id at index=%s', index)
                continue
            try:
                track_id = int(track_id)
            except Exception:
                continue
            if track_id < 0:
                logger.info('Using fallback YOLO track id %s for detection index=%s', track_id, index)

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

    def _resolve_track_id(self, box: Any, index: int) -> Optional[int]:
        """Return tracker id, or a stable fallback id when the tracker has not assigned one."""
        track_identifier = getattr(box, 'id', None)
        if track_identifier is not None:
            try:
                return int(track_identifier.item() if hasattr(track_identifier, 'item') else track_identifier)
            except Exception:
                logger.debug('Invalid YOLO track id: %s', track_identifier)

        coords = getattr(box, 'xyxy', None)
        if coords is None:
            return None
        try:
            values = coords.tolist()[0]
        except Exception:
            return None
        if len(values) < 4:
            return None
        center_x = int((float(values[0]) + float(values[2])) / 2.0 / 32.0)
        center_y = int((float(values[1]) + float(values[3])) / 2.0 / 32.0)
        width = int(abs(float(values[2]) - float(values[0])) / 32.0)
        height = int(abs(float(values[3]) - float(values[1])) / 32.0)
        return -abs(hash((center_x, center_y, width, height, index)) % 1_000_000)

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

        snapshot_raw = self._resize_snapshot_frame(raw_frame)
        snapshot_annotated = self._resize_snapshot_frame(annotated_frame)
        raw_b64 = self._encode_frame(snapshot_raw, quality=80)
        annotated_b64 = self._encode_frame(snapshot_annotated, quality=85)
        if self._telemetry is not None and raw_b64 and annotated_b64:
            try:
                response = self._telemetry.emit(
                    kind='telemetry.yolo.snapshot',
                    source='yolo',
                    body={
                        'track_id': int(track_id),
                        'timestamp': timestamp.isoformat(),
                        'ts': unix_time,
                        'device_id': device_id,
                        'location': location,
                        'image_name': image_name,
                        'raw_b64': raw_b64,
                        'annotated_b64': annotated_b64,
                    },
                )
            except IPCUnavailableError as exc:
                self._log_telemetry_unavailable(exc)
                return
            except Exception:
                logger.exception(
                    'Telemetry yolo snapshot emit failed: track=%s name=%s',
                    track_id,
                    image_name,
                )
                return
            response_body = response.get('body') if isinstance(response, dict) else None
            if not isinstance(response_body, dict) or not response_body.get('ok'):
                logger.error(
                    'Telemetry yolo snapshot failed: track=%s name=%s response=%s',
                    track_id,
                    image_name,
                    response,
                )
            else:
                logger.info('Telemetry yolo snapshot accepted: track=%s name=%s', track_id, image_name)
            return

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
        if self._telemetry is not None:
            return fallback
        if self._db_worker is not None:
            try:
                latest_rfid = self._db_worker.call_data_service('get_latest_rfid_card')
                if latest_rfid:
                    return latest_rfid
            except Exception:
                logger.debug('Failed to resolve latest RFID for YOLO snapshot location', exc_info=True)
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

    def _resize_snapshot_frame(self, frame: Any) -> Any:
        """Resize snapshots before encoding to keep DB and MQTT payloads bounded."""
        assert cv2 is not None
        target_width = int(self._settings.yolo_frame_width or 640)
        target_height = int(self._settings.yolo_frame_height or 360)
        if target_width <= 0 or target_height <= 0:
            return frame
        try:
            height, width = frame.shape[:2]
        except Exception:
            return frame
        if width == target_width and height == target_height:
            return frame
        return cv2.resize(frame, (target_width, target_height), interpolation=cv2.INTER_AREA)

    def _publish_payload(self, topic: str, payload: Dict[str, Any], description: str) -> None:
        """Publish payload to MQTT with logging."""
        if self._mqtt is None:
            logger.debug('Skip MQTT publish for %s because mqtt_manager is None', description)
            return
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
        if self._db_worker is not None:
            try:
                entity = ImageData(
                    timestamp=timestamp,
                    device_id=device_id,
                    image_name=image_name,
                    image_data=image_data,
                    location=location,
                )
                self._db_worker.call_data_service('create_image_data', [entity])
                logger.info(
                    'Snapshot stored in database: name=%s device=%s location=%s',
                    image_name,
                    device_id,
                    location,
                )
            except Exception:
                logger.exception('Failed to store snapshot %s', image_name)
            return
        logger.error(
            'Skipping snapshot persistence because db_worker is not configured name=%s device=%s',
            image_name,
            device_id,
        )

    def _write_rtsp_frame(self, annotated_frame: Any) -> None:
        """Forward annotated frames through RTSP output backend."""
        if self._rtsp_output is None or cv2 is None:
            return
        width = int(self._settings.yolo_frame_width or annotated_frame.shape[1])
        height = int(self._settings.yolo_frame_height or annotated_frame.shape[0])
        try:
            frame_height, frame_width = annotated_frame.shape[:2]
        except Exception:
            return
        if frame_width == width and frame_height == height:
            resized = annotated_frame
        else:
            interpolation = cv2.INTER_AREA if width < frame_width or height < frame_height else cv2.INTER_LINEAR
            resized = cv2.resize(annotated_frame, (width, height), interpolation=interpolation)
        self._rtsp_output.write_frame(resized)

    def _start_rtsp_output(self) -> None:
        """Start RTSP output backend (GStreamer or FFmpeg)."""
        if not self._settings.yolo_ffmpeg_enabled:
            return
        output_url = (self._settings.yolo_rtsp_output or '').strip()
        if not output_url:
            return
        if self._rtsp_output is not None:
            return

        frame_width = int(self._settings.yolo_frame_width or 640)
        frame_height = int(self._settings.yolo_frame_height or 360)
        fps = int(self._settings.yolo_fps or 25)
        backend = self._resolve_rtsp_backend()
        config = RtspOutputConfig(
            output_url=output_url,
            width=frame_width,
            height=frame_height,
            fps=fps,
            backend=backend,
            low_latency=bool(self._settings.yolo_rtsp_low_latency),
            encoder=(self._settings.yolo_gst_encoder or 'x264enc').strip(),
            encoder_props=(self._settings.yolo_gst_encoder_props or '').strip(),
        )
        self._rtsp_output = create_rtsp_output(config)
        if not self._rtsp_output.start():
            logger.warning('RTSP output backend not started (backend=%s, url=%s)', backend, output_url)

    def _stop_rtsp_output(self) -> None:
        """Stop RTSP output backend if running."""
        output = self._rtsp_output
        self._rtsp_output = None
        if output:
            output.stop()

    def _resolve_rtsp_backend(self) -> str:
        """Resolve RTSP backend selection."""
        backend = (self._settings.yolo_rtsp_backend or 'gstreamer').strip().lower()
        if backend not in {'gstreamer', 'ffmpeg'}:
            logger.warning('Unknown YOLO_RTSP_BACKEND=%s, fallback to gstreamer', backend)
            backend = 'gstreamer'
        return backend

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
