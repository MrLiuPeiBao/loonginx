from __future__ import annotations

from datetime import datetime

from app.core.config import Settings
from app.ipc import IPCUnavailableError
from app.services import yolo_service
from app.services.yolo_service import YOLOStreamService, _next_rtsp_failure_backoff


def test_yolo_compute_device_defaults_to_auto():
    settings = Settings(_env_file=None, yolo_compute_device='auto')

    assert settings.yolo_compute_device == 'auto'


def test_yolo_compute_device_uses_configured_cuda_when_available(monkeypatch):
    class FakeCuda:
        @staticmethod
        def is_available() -> bool:
            return True

    class FakeTorch:
        cuda = FakeCuda()

    monkeypatch.setattr(yolo_service, 'torch', FakeTorch())
    settings = Settings(_env_file=None, yolo_compute_device='cuda:0')
    service = YOLOStreamService(settings)

    assert service._device == 'cuda:0'


def test_yolo_compute_device_falls_back_to_cpu_when_cuda_unavailable(monkeypatch):
    class FakeCuda:
        @staticmethod
        def is_available() -> bool:
            return False

    class FakeTorch:
        cuda = FakeCuda()

    monkeypatch.setattr(yolo_service, 'torch', FakeTorch())
    settings = Settings(_env_file=None, yolo_compute_device='cuda:0')
    service = YOLOStreamService(settings)

    assert service._device == 'cpu'


def test_yolo_person_confidence_defaults_to_confidence_threshold() -> None:
    settings = Settings(_env_file=None)

    assert settings.yolo_person_confidence is None


def test_yolo_read_failure_reconnect_threshold_is_configurable() -> None:
    settings = Settings(_env_file=None, yolo_read_failure_reconnect_threshold=3)

    assert settings.yolo_read_failure_reconnect_threshold == 3


def test_yolo_inference_size_is_configurable() -> None:
    settings = Settings(_env_file=None, yolo_inference_size=512)

    assert settings.yolo_inference_size == 512


def test_yolo_watchdog_thresholds_are_configurable() -> None:
    settings = Settings(
        _env_file=None,
        yolo_inference_timeout_seconds=6.5,
        yolo_inference_timeout_consecutive_limit=2,
        yolo_watchdog_reconnect_cooldown_seconds=1.5,
        yolo_watchdog_max_stream_restarts=4,
    )

    assert settings.yolo_inference_timeout_seconds == 6.5
    assert settings.yolo_inference_timeout_consecutive_limit == 2
    assert settings.yolo_watchdog_reconnect_cooldown_seconds == 1.5
    assert settings.yolo_watchdog_max_stream_restarts == 4


def test_yolo_rtsp_failure_backoff_is_configurable_and_bounded() -> None:
    settings = Settings(
        _env_file=None,
        yolo_rtsp_failure_backoff_initial_seconds=5.0,
        yolo_rtsp_failure_backoff_max_seconds=20.0,
    )

    first = _next_rtsp_failure_backoff(
        0.0,
        initial_seconds=settings.yolo_rtsp_failure_backoff_initial_seconds,
        max_seconds=settings.yolo_rtsp_failure_backoff_max_seconds,
    )
    second = _next_rtsp_failure_backoff(
        first,
        initial_seconds=settings.yolo_rtsp_failure_backoff_initial_seconds,
        max_seconds=settings.yolo_rtsp_failure_backoff_max_seconds,
    )
    capped = _next_rtsp_failure_backoff(
        second,
        initial_seconds=settings.yolo_rtsp_failure_backoff_initial_seconds,
        max_seconds=settings.yolo_rtsp_failure_backoff_max_seconds,
    )

    assert first == 5.0
    assert second == 10.0
    assert capped == 20.0


def test_yolo_fallback_track_id_from_box_coordinates() -> None:
    class FakeCoords:
        def tolist(self):
            return [[100.0, 200.0, 300.0, 600.0]]

    class FakeBox:
        id = None
        xyxy = FakeCoords()

    service = YOLOStreamService(Settings(_env_file=None))

    track_id = service._resolve_track_id(FakeBox(), 0)

    assert isinstance(track_id, int)
    assert track_id < 0


def test_yolo_snapshot_resize_uses_configured_frame_size(monkeypatch) -> None:
    class FakeCV2:
        INTER_AREA = 3

        @staticmethod
        def resize(frame, size, interpolation=None):
            frame.resized_to = size
            frame.interpolation = interpolation
            return frame

    class FakeFrame:
        shape = (1520, 2688, 3)

    monkeypatch.setattr(yolo_service, 'cv2', FakeCV2())
    service = YOLOStreamService(
        Settings(_env_file=None, yolo_frame_width=640, yolo_frame_height=360)
    )
    frame = FakeFrame()

    resized = service._resize_snapshot_frame(frame)

    assert resized is frame
    assert frame.resized_to == (640, 360)
    assert frame.interpolation == FakeCV2.INTER_AREA


def test_yolo_emit_snapshot_skips_when_telemetry_pipe_unavailable(monkeypatch) -> None:
    class FailingTelemetry:
        calls = 0

        def emit(self, **kwargs):
            self.calls += 1
            raise IPCUnavailableError('missing pipe')

    telemetry = FailingTelemetry()
    service = YOLOStreamService(
        Settings(_env_file=None, yolo_frame_width=640, yolo_frame_height=360),
        telemetry_client=telemetry,
    )

    monkeypatch.setattr(yolo_service, 'cv2', object())
    monkeypatch.setattr(service, '_resize_snapshot_frame', lambda frame: frame)
    monkeypatch.setattr(service, '_encode_frame', lambda frame, *, quality: 'ZmFrZQ==')

    service._emit_snapshots(1, object(), object(), datetime.now())

    assert telemetry.calls == 1


def test_yolo_watchdog_status_exposes_runtime_counters() -> None:
    service = YOLOStreamService(
        Settings(_env_file=None, yolo_enabled=True, yolo_inference_timeout_seconds=9.0),
    )
    service._watchdog_update(stream_restart_count=2, consecutive_inference_timeouts=1)

    status = service.get_watchdog_status()

    assert status['enabled'] is True
    assert status['stream_restart_count'] == 2
    assert status['consecutive_inference_timeouts'] == 1
    assert status['inference_timeout_seconds'] == 9.0


def test_yolo_process_stream_returns_watchdog_timeout_after_consecutive_timeouts(monkeypatch) -> None:
    class FakeCV2:
        INTER_AREA = 3
        INTER_LINEAR = 1

        @staticmethod
        def resize(frame, size, interpolation=None):
            return frame

    class FakeCap:
        def read(self):
            return True, self

        def copy(self):
            return self

        @property
        def shape(self):
            return (720, 1280, 3)

    settings = Settings(
        _env_file=None,
        yolo_enabled=True,
        yolo_inference_timeout_seconds=0.5,
        yolo_inference_timeout_consecutive_limit=1,
        yolo_inference_interval=0.05,
    )
    service = YOLOStreamService(settings)
    service._model = object()

    monkeypatch.setattr(yolo_service, 'cv2', FakeCV2())
    monkeypatch.setattr(service, '_write_rtsp_frame', lambda frame: None)
    monkeypatch.setattr(service, '_start_rtsp_output', lambda: None)
    monkeypatch.setattr(service, '_stop_rtsp_output', lambda: None)
    monkeypatch.setattr(service, '_wait_with_stop', lambda duration: None)

    sequence = iter([0.0, 0.0, 0.1, 0.8, 0.8, 0.8, 0.8, 1.6, 1.6, 1.6])
    monkeypatch.setattr(yolo_service.time, 'time', lambda: next(sequence, 1.6))

    class WatchdogEvent:
        def __init__(self):
            self._value = False

        def is_set(self):
            return self._value

        def set(self):
            self._value = True

        def clear(self):
            self._value = False

    class FakeThread:
        def __init__(self, target=None, args=(), name=None, daemon=None):
            self._target = target
            self._args = args

        def start(self):
            # Keep inference_busy set to let watchdog detect timeout.
            return None

    monkeypatch.setattr(yolo_service.threading, 'Event', lambda: WatchdogEvent())
    monkeypatch.setattr(yolo_service.threading, 'Thread', FakeThread)

    reason = service._process_stream(FakeCap())

    assert reason == 'watchdog_timeout'
