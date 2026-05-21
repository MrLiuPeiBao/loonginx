from __future__ import annotations

from app.core.config import Settings
from app.runtime.media_write_worker import MediaWriteWorker
from app.runtime.telemetry_bridge import TelemetryBridgeServer


class _DummyDBWorker:
    def call_data_service(self, *args, **kwargs):  # pragma: no cover - not used in this unit
        return []


class _DummyMQTTWorker:
    def publish(self, *args, **kwargs):  # pragma: no cover - not used in this unit
        return True


def test_telemetry_bridge_ack_is_queue_only():
    media_worker = MediaWriteWorker(db_worker=_DummyDBWorker(), mqtt_worker=_DummyMQTTWorker(), max_queue_size=2)
    bridge = TelemetryBridgeServer(
        settings=Settings(_env_file=None),
        media_write_worker=media_worker,
    )

    response = bridge._handle_message(
        {
            "id": "msg-1",
            "kind": "telemetry.audio.clip",
            "body": {
                "timestamp": "2026-05-07T00:00:00",
                "device_id": "mic-1",
                "location": "loc-1",
                "audio_name": "sample.wav",
                "audio_hex": "00",
            },
        }
    )

    assert response["body"]["ok"] is True
    status = media_worker.get_status()
    assert status["media_queue_size"] == 1


def test_media_write_worker_exposes_queue_status():
    worker = MediaWriteWorker(db_worker=_DummyDBWorker(), mqtt_worker=_DummyMQTTWorker(), max_queue_size=3)

    assert worker.enqueue(kind="telemetry.yolo.snapshot", body={"timestamp": "2026-05-07T00:00:00"})
    status = worker.get_status()

    assert status["media_queue_capacity"] == 3
    assert status["media_queue_size"] == 1
    assert status["media_dropped"] == 0


def test_media_write_worker_drops_oldest_when_queue_full():
    worker = MediaWriteWorker(db_worker=_DummyDBWorker(), mqtt_worker=_DummyMQTTWorker(), max_queue_size=2)

    assert worker.enqueue(kind="telemetry.yolo.snapshot", body={"seq": 1})
    assert worker.enqueue(kind="telemetry.yolo.snapshot", body={"seq": 2})
    assert worker.enqueue(kind="telemetry.yolo.snapshot", body={"seq": 3})

    status = worker.get_status()
    assert status["media_queue_capacity"] == 2
    assert status["media_queue_size"] == 2
    assert status["media_dropped"] == 1
