from __future__ import annotations

import json
import logging
import queue
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

from app.core.constants import MQTT_TOPICS
from app.db.models import AudioData, ImageData
from app.runtime.db_worker import DBWorker
from app.runtime.mqtt_worker import MQTTWorker
from app.services.alarm_publisher import build_alarm_event, publish_alarm_event

logger = logging.getLogger(__name__)


@dataclass
class _MediaJob:
    kind: str
    body: Dict[str, Any]


class MediaWriteWorker:
    """Asynchronous media writer to isolate heavy media writes from API control-plane."""

    def __init__(self, *, db_worker: DBWorker, mqtt_worker: MQTTWorker, max_queue_size: int = 1000):
        self._db_worker = db_worker
        self._mqtt_worker = mqtt_worker
        self._queue: queue.Queue[_MediaJob] = queue.Queue(maxsize=max(1, int(max_queue_size)))
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._stats_lock = threading.Lock()
        self._processed = 0
        self._failed = 0
        self._dropped = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="media-write-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop_event.set()
        thread = self._thread
        self._thread = None
        if thread and thread.is_alive():
            thread.join(timeout=timeout)

    def enqueue(self, *, kind: str, body: Dict[str, Any]) -> bool:
        job = _MediaJob(kind=str(kind), body=dict(body or {}))
        try:
            self._queue.put_nowait(job)
            return True
        except queue.Full:
            dropped_job = self._drop_oldest_job()
            if dropped_job is None:
                return False
            try:
                self._queue.put_nowait(job)
                logger.warning(
                    'Media queue full, dropped oldest job kind=%s and accepted new kind=%s',
                    dropped_job.kind,
                    job.kind,
                )
                return True
            except queue.Full:
                return False

    def get_status(self) -> Dict[str, object]:
        with self._stats_lock:
            processed = self._processed
            failed = self._failed
        return {
            "media_queue_size": int(self._queue.qsize()),
            "media_queue_capacity": int(self._queue.maxsize),
            "media_processed": int(processed),
            "media_failed": int(failed),
            "media_dropped": int(self._dropped),
            "media_writer_alive": bool(self._thread and self._thread.is_alive()),
        }

    def _drop_oldest_job(self) -> Optional[_MediaJob]:
        try:
            oldest = self._queue.get_nowait()
        except queue.Empty:
            return None
        with self._stats_lock:
            self._dropped += 1
        return oldest

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                job = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                if job.kind == "telemetry.yolo.snapshot":
                    self._process_yolo_snapshot(job.body)
                elif job.kind == "telemetry.audio.clip":
                    self._process_audio_clip(job.body)
                with self._stats_lock:
                    self._processed += 1
            except Exception:
                with self._stats_lock:
                    self._failed += 1
                logger.exception("Media write job failed kind=%s", job.kind)

    def _process_yolo_snapshot(self, body: Dict[str, Any]) -> None:
        timestamp = datetime.fromisoformat(str(body["timestamp"]))
        raw_payload = {
            "track_id": int(body["track_id"]),
            "timestamp": str(body["timestamp"]),
            "ts": float(body.get("ts") or timestamp.timestamp()),
            "device_id": str(body["device_id"]),
            "location": str(body["location"]),
            "image_name": str(body["image_name"]),
            "img": str(body["raw_b64"]),
        }
        annotated_payload = {
            "track_id": int(body["track_id"]),
            "timestamp": str(body["timestamp"]),
            "ts": float(body.get("ts") or timestamp.timestamp()),
            "device_id": str(body["device_id"]),
            "location": str(body["location"]),
            "img": str(body["annotated_b64"]),
        }
        self._mqtt_worker.publish(
            MQTT_TOPICS["person_image"],
            payload=json.dumps(raw_payload, ensure_ascii=False).encode("utf-8"),
            qos=1,
        )
        self._mqtt_worker.publish(
            MQTT_TOPICS["annotated_person_image"],
            payload=json.dumps(annotated_payload, ensure_ascii=False).encode("utf-8"),
            qos=1,
        )

        entity = ImageData(
            timestamp=timestamp,
            device_id=str(body["device_id"]),
            image_name=str(body["image_name"]),
            image_data=str(body["raw_b64"]),
            location=str(body["location"]),
        )
        self._db_worker.call_data_service("create_image_data", [entity])
        publish_alarm_event(
            self._mqtt_worker,
            build_alarm_event(
                source="yolo_person",
                timestamp=timestamp,
                device_id=str(body["device_id"]),
                location=str(body["location"]),
                payload={
                    "track_id": int(body["track_id"]),
                    "image_name": str(body["image_name"]),
                    "topic": MQTT_TOPICS["person_image"],
                },
            ),
        )

    def _process_audio_clip(self, body: Dict[str, Any]) -> None:
        timestamp = datetime.fromisoformat(str(body["timestamp"]))
        audio_name = str(body["audio_name"])
        wav_bytes = bytes.fromhex(str(body["audio_hex"]))
        entity = AudioData(
            timestamp=timestamp,
            device_id=str(body["device_id"]),
            audio_name=audio_name,
            audio_data=wav_bytes,
            location=str(body["location"]),
        )
        stored = self._db_worker.call_data_service("create_audio_data", [entity], commit=True)
        stored_id = stored[0].id if stored else None
        publish_alarm_event(
            self._mqtt_worker,
            build_alarm_event(
                source="audio_threshold",
                timestamp=timestamp,
                device_id=str(body["device_id"]),
                location=str(body["location"]),
                payload={
                    "audio_id": stored_id,
                    "audio_name": audio_name,
                    "bytes": len(wav_bytes),
                    "metrics": dict(body.get("metrics") or {}),
                },
            ),
        )
