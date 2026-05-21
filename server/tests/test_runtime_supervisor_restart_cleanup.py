from __future__ import annotations

from types import SimpleNamespace

from app.core.config import Settings
from app.services.runtime_supervisor import RuntimeSupervisor


def test_ensure_worker_process_cleans_stale_worker_before_start(monkeypatch):
    supervisor = RuntimeSupervisor(
        mqtt_manager=SimpleNamespace(is_connected=True, connect=lambda: None),
        yolo_service=SimpleNamespace(start=lambda: None),
        audio_service=SimpleNamespace(start=lambda: None),
        read_db_worker=SimpleNamespace(),
        write_db_worker=SimpleNamespace(),
        settings=Settings(_env_file=None),
    )
    cleaned: list[str] = []
    started: list[tuple[list[str], object]] = []

    monkeypatch.setattr(
        supervisor,
        '_stop_stale_worker_processes',
        lambda *, module: cleaned.append(module),
    )

    class FakeProcess:
        def poll(self):
            return None

    monkeypatch.setattr(
        'app.services.runtime_supervisor.subprocess.Popen',
        lambda args, cwd=None: started.append((args, cwd)) or FakeProcess(),
    )

    proc = supervisor._ensure_worker_process(
        name='yolo-worker',
        module='app.services.yolo_worker',
        existing=None,
    )

    assert proc is not None
    assert cleaned == ['app.services.yolo_worker']
    assert started
    assert started[0][0][-2:] == ['-m', 'app.services.yolo_worker']


def test_runtime_changes_restart_media_gateway(monkeypatch):
    settings = Settings(
        _env_file=None,
        media_gateway_enabled=True,
        media_gateway_type='custom',
        media_gateway_exec='C:\\tools\\go2rtc.exe',
        media_gateway_args='-config C:\\tools\\go2rtc.yaml',
    )
    supervisor = RuntimeSupervisor(
        mqtt_manager=SimpleNamespace(is_connected=True, connect=lambda: None),
        yolo_service=SimpleNamespace(start=lambda: None),
        audio_service=SimpleNamespace(start=lambda: None),
        read_db_worker=SimpleNamespace(),
        write_db_worker=SimpleNamespace(),
        settings=settings,
    )
    restarted: list[str] = []
    yolo_restarted: list[str] = []
    audio_restarted: list[str] = []
    monkeypatch.setattr(
        supervisor,
        'restart_media_gateway_worker',
        lambda *, reason='': restarted.append(reason),
    )
    monkeypatch.setattr(
        supervisor,
        'restart_yolo_worker',
        lambda *, reason='': yolo_restarted.append(reason),
    )
    monkeypatch.setattr(
        supervisor,
        'restart_audio_worker',
        lambda *, reason='': audio_restarted.append(reason),
    )

    supervisor._handle_runtime_changes(['MEDIA_GATEWAY_RELAY_RTSP', 'YOLO_RTSP_INPUT'])

    assert restarted == ['MEDIA_GATEWAY_* updated']
    assert yolo_restarted == ['YOLO_* updated', 'MEDIA_GATEWAY_* updated']
    assert audio_restarted == ['MEDIA_GATEWAY_* updated']


def test_media_gateway_stops_conflicting_yolo_rtsp_port(monkeypatch):
    settings = Settings(
        _env_file=None,
        media_gateway_enabled=True,
        media_gateway_type='go2rtc',
        media_gateway_relay_rtsp='rtsp://127.0.0.1:8554/cam01',
        yolo_rtsp_output='rtsp://192.168.0.100:8554/yolo',
    )
    supervisor = RuntimeSupervisor(
        mqtt_manager=SimpleNamespace(is_connected=True, connect=lambda: None),
        yolo_service=SimpleNamespace(start=lambda: None),
        audio_service=SimpleNamespace(start=lambda: None),
        read_db_worker=SimpleNamespace(),
        write_db_worker=SimpleNamespace(),
        settings=settings,
    )
    restarted: list[str] = []
    supervisor._yolo_process = SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(
        supervisor,
        'restart_yolo_worker',
        lambda *, reason='': restarted.append(reason),
    )

    supervisor._stop_conflicting_rtsp_output_bindings()

    assert restarted == ['rtsp port conflict with media gateway']
