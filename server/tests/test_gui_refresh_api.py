from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import get_audio_service, get_data_service, get_settings, router
from app.core.config import Settings
from app.db.models import AudioData, ImageData


class _FakeAudioService:
    def get_metrics(self, limit: int = 100):
        return [
            {'timestamp': datetime.now().isoformat(timespec='seconds'), 'wav_rms': 12.0, 'wav_peak': 200.0},
            {'timestamp': datetime.now().isoformat(timespec='seconds'), 'wav_rms': 14.0, 'wav_peak': 220.0},
        ][:limit]


class _FakeDataService:
    async def count_sensor_data(self, **kwargs):
        return 1

    async def list_sensor_data(self, **kwargs):
        return [{'device_id': 'sensor-01', 'location': 'A', 'timestamp': '2026-05-07T15:10:20'}]

    async def count_bms_data(self, **kwargs):
        return 1

    async def list_bms_data(self, **kwargs):
        return [{'device_id': 'bms-01', 'location': 'A', 'timestamp': '2026-05-07T15:10:20'}]

    async def count_rfid_data(self, **kwargs):
        return 1

    async def list_rfid_data(self, **kwargs):
        return [{'device_id': 'rfid-01', 'location': 'A', 'timestamp': '2026-05-07T15:10:20'}]

    async def count_command_logs(self, **kwargs):
        return 1

    async def list_command_logs(self, **kwargs):
        return [{'id': 1, 'payload': '01 02 03', 'timestamp': '2026-05-07T15:10:20'}]

    async def count_alarm_records(self, **kwargs):
        return 1

    async def list_alarm_records(self, **kwargs):
        return [{'id': 1, 'sensor_name': 'CO', 'timestamp': '2026-05-07T15:10:20'}]

    async def count_image_data(self, **kwargs):
        return 1

    async def list_image_data(self, **kwargs):
        return [
            ImageData(
                id=11,
                timestamp=datetime(2026, 5, 7, 15, 10, 20),
                device_id='camera-01',
                image_name='img.jpg',
                image_data='aW1hZ2U=',
                location='gate',
            )
        ]

    async def count_audio_data(self, **kwargs):
        return 1

    async def list_audio_data(self, **kwargs):
        return [
            AudioData(
                id=21,
                timestamp=datetime(2026, 5, 7, 15, 10, 20),
                device_id='mic-01',
                audio_name='a.wav',
                audio_data=b'RIFFdata',
                location='gate',
            )
        ]


class _FakeSession:
    def exec(self, statement):
        text = str(statement)
        if 'count(*)' in text and 'sensor_data' in text:
            return SimpleNamespace(one=lambda: 1)
        if 'count(*)' in text and 'bms_data' in text:
            return SimpleNamespace(one=lambda: 1)
        if 'count(*)' in text and 'rfid_data' in text:
            return SimpleNamespace(one=lambda: 1)
        if 'count(*)' in text and 'command_logs' in text:
            return SimpleNamespace(one=lambda: 1)
        if 'count(*)' in text and 'alarm_records' in text:
            return SimpleNamespace(one=lambda: 1)
        if 'count(*)' in text and 'image_data' in text:
            return SimpleNamespace(one=lambda: 1)
        if 'count(*)' in text and 'audio_data' in text:
            return SimpleNamespace(one=lambda: 1)
        if 'FROM sensor_data' in text:
            return [{'device_id': 'sensor-01', 'location': 'A', 'timestamp': '2026-05-07T15:10:20'}]
        if 'FROM bms_data' in text:
            return [{'device_id': 'bms-01', 'location': 'A', 'timestamp': '2026-05-07T15:10:20'}]
        if 'FROM rfid_data' in text:
            return [{'device_id': 'rfid-01', 'location': 'A', 'timestamp': '2026-05-07T15:10:20'}]
        if 'FROM command_logs' in text:
            return [{'id': 1, 'payload': '01 02 03', 'timestamp': '2026-05-07T15:10:20'}]
        if 'FROM alarm_records' in text:
            return [{'id': 1, 'sensor_name': 'CO', 'timestamp': '2026-05-07T15:10:20'}]
        if 'FROM sensor_config' in text:
            return []
        if 'FROM image_data' in text:
            return [
                ImageData(
                    id=11,
                    timestamp=datetime(2026, 5, 7, 15, 10, 20),
                    device_id='camera-01',
                    image_name='img.jpg',
                    image_data='aW1hZ2U=',
                    location='gate',
                )
            ]
        if 'FROM audio_data' in text:
            return [
                AudioData(
                    id=21,
                    timestamp=datetime(2026, 5, 7, 15, 10, 20),
                    device_id='mic-01',
                    audio_name='a.wav',
                    audio_data=b'RIFFdata',
                    location='gate',
                )
            ]
        return []


def _build_test_app(*, settings: Settings | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    async def _db_call_async(func, *_args, **_kwargs):
        return func(_FakeSession())

    async def _db_call_data_service_async(*_args, **_kwargs):
        return []

    app.state.read_db_worker = SimpleNamespace(
        call_async=_db_call_async,
        call_data_service_async=_db_call_data_service_async,
    )
    app.state.write_db_worker = SimpleNamespace(call_data_service_async=_db_call_data_service_async)
    app.state.db_worker = app.state.write_db_worker
    app.state.audio = _FakeAudioService()
    app.state.supervisor = SimpleNamespace(get_status=lambda: {'yolo_process_alive': True, 'audio_process_alive': True})
    app.dependency_overrides[get_data_service] = lambda: _FakeDataService()
    if settings is None:
        settings = Settings(_env_file=None)
    app.dependency_overrides[get_settings] = lambda: settings
    return app


def test_gui_refresh_returns_partial_safe_payload():
    app = _build_test_app()

    response = TestClient(app).get('/api/gui/refresh')

    assert response.status_code == 200
    payload = response.json()
    assert payload['status'] == 'ok'
    assert payload['sections']['sensors']['ok'] is True
    assert payload['sections']['configs']['ok'] is True
    assert payload['sections']['images']['ok'] is True
    assert payload['sections']['audio']['ok'] is True
    assert payload['sections']['audio']['data']['items'][0]['audio_data'] == ''
    assert payload['sections']['images']['data']['items'][0]['image_data'] == ''


def test_gui_refresh_uses_single_db_batch_call():
    app = _build_test_app()
    calls: list[str] = []

    async def _db_call_async(func, *_args, **_kwargs):
        class _DummySession:
            pass

        calls.append('batch')
        return func(_DummySession())

    app.state.read_db_worker = SimpleNamespace(
        call_async=_db_call_async,
        call_data_service_async=lambda *_args, **_kwargs: [],
    )

    response = TestClient(app).get('/api/gui/refresh')

    assert response.status_code == 200
    assert calls == ['batch']


def test_health_workers_reports_shared_rtsp_source():
    settings = Settings(
        _env_file=None,
        yolo_enabled=True,
        audio_enabled=True,
        yolo_rtsp_input='rtsp://admin:pwd@192.168.0.101:554/Streaming/Channels/101',
        audio_rtsp_input='rtsp://admin:pwd@192.168.0.101:554/Streaming/Channels/101',
    )
    app = _build_test_app(settings=settings)

    response = TestClient(app).get('/api/health/workers')

    assert response.status_code == 200
    payload = response.json()
    assert payload['rtsp']['shared_source'] is True
    assert payload['rtsp']['expected_consumers'] == 2
    assert payload['workers']['yolo']['enabled'] is True
    assert payload['workers']['audio']['enabled'] is True


def test_health_workers_prefers_gateway_rtsp_when_enabled():
    settings = Settings(
        _env_file=None,
        yolo_enabled=True,
        audio_enabled=True,
        yolo_rtsp_input='rtsp://admin:pwd@192.168.0.101:554/Streaming/Channels/101',
        audio_rtsp_input='rtsp://admin:pwd@192.168.0.101:554/Streaming/Channels/101',
        media_gateway_enabled=True,
        media_gateway_type='go2rtc',
        media_gateway_relay_rtsp='rtsp://127.0.0.1:8554/cam01',
        media_gateway_control_panel_rtsp='rtsp://127.0.0.1:8554/cam01',
    )
    app = _build_test_app(settings=settings)

    response = TestClient(app).get('/api/health/workers')

    assert response.status_code == 200
    payload = response.json()
    assert payload['workers']['media_gateway']['enabled'] is True
    assert payload['workers']['media_gateway']['type'] == 'go2rtc'
    assert payload['workers']['yolo']['rtsp_input_direct'] == 'rtsp://192.168.0.101:554/Streaming/Channels/101'
    assert payload['workers']['audio']['rtsp_input_direct'] == 'rtsp://192.168.0.101:554/Streaming/Channels/101'
    assert payload['workers']['yolo']['rtsp_input'] == 'rtsp://127.0.0.1:8554/cam01'
    assert payload['workers']['audio']['rtsp_input'] == 'rtsp://127.0.0.1:8554/cam01'
    assert payload['rtsp']['gateway_rewrite_active'] is True
    assert payload['rtsp']['suggested_control_panel_rtsp'] == 'rtsp://127.0.0.1:8554/cam01'


def test_runtime_media_status_reports_online_flags(monkeypatch):
    settings = Settings(
        _env_file=None,
        yolo_enabled=True,
        yolo_run_mode='process',
        audio_enabled=True,
        audio_run_mode='process',
        yolo_rtsp_input='rtsp://admin:pwd@192.168.0.101:554/Streaming/Channels/101',
        audio_rtsp_input='rtsp://admin:pwd@192.168.0.101:554/Streaming/Channels/101',
        media_gateway_enabled=True,
        media_gateway_type='go2rtc',
        media_gateway_http_api='http://127.0.0.1:1984',
        media_gateway_relay_rtsp='rtsp://127.0.0.1:8554/cam01',
        media_gateway_control_panel_rtsp='rtsp://127.0.0.1:8554/cam01',
        yolo_rtsp_output='rtsp://192.168.0.100:8555/yolo',
    )
    app = _build_test_app(settings=settings)
    app.state.media_write_worker = SimpleNamespace(
        get_status=lambda: {
            'media_queue_size': 3,
            'media_queue_capacity': 1000,
            'media_writer_alive': True,
        }
    )
    app.state.supervisor = SimpleNamespace(
        get_status=lambda: {
            'yolo_process_alive': True,
            'audio_process_alive': True,
            'media_gateway_process_alive': True,
        }
    )
    app.state.yolo = SimpleNamespace(
        get_watchdog_status=lambda: {
            'last_inference_finished_at': datetime.now().isoformat(timespec='seconds'),
        }
    )

    from app.api import routes as api_routes

    with monkeypatch.context() as m:
        m.setattr(api_routes, '_probe_go2rtc_stream_online', lambda **kwargs: True)
        response = TestClient(app).get('/api/runtime/media-status')

    assert response.status_code == 200
    payload = response.json()
    assert payload['camera_online'] is True
    assert payload['go2rtc_online'] is True
    assert payload['yolo_online'] is True
    assert payload['audio_online'] is True
    assert payload['media_queue_size'] == 3
    assert payload['media_writer_alive'] is True
    assert payload['rtsp']['yolo_output'] == 'rtsp://192.168.0.100:8555/yolo'


def test_runtime_media_status_reports_offline_when_gateway_probe_fails(monkeypatch):
    settings = Settings(
        _env_file=None,
        yolo_enabled=True,
        yolo_run_mode='process',
        audio_enabled=True,
        audio_run_mode='process',
        yolo_rtsp_input='rtsp://127.0.0.1:8554/cam01',
        audio_rtsp_input='rtsp://127.0.0.1:8554/cam01',
        media_gateway_enabled=True,
        media_gateway_type='go2rtc',
        media_gateway_http_api='http://127.0.0.1:1984',
        media_gateway_relay_rtsp='rtsp://127.0.0.1:8554/cam01',
        media_gateway_control_panel_rtsp='rtsp://127.0.0.1:8554/cam01',
    )
    app = _build_test_app(settings=settings)
    app.state.supervisor = SimpleNamespace(
        get_status=lambda: {
            'yolo_process_alive': True,
            'audio_process_alive': True,
            'media_gateway_process_alive': True,
        }
    )
    app.state.audio = SimpleNamespace(get_metrics=lambda limit=1: [])
    app.state.yolo = SimpleNamespace(get_watchdog_status=lambda: {'last_inference_finished_at': None})

    from app.api import routes as api_routes

    monkeypatch.setattr(api_routes, '_probe_go2rtc_stream_online', lambda **kwargs: False)

    response = TestClient(app).get('/api/runtime/media-status')

    assert response.status_code == 200
    payload = response.json()
    assert payload['go2rtc_online'] is False
    assert payload['camera_online'] is False
    assert payload['yolo_online'] is False
    assert payload['audio_online'] is False


def test_runtime_media_status_audio_offline_when_metrics_stale(monkeypatch):
    settings = Settings(
        _env_file=None,
        yolo_enabled=False,
        audio_enabled=True,
        audio_run_mode='process',
        audio_rtsp_input='rtsp://127.0.0.1:8554/cam01',
        media_gateway_enabled=False,
    )
    app = _build_test_app(settings=settings)
    app.state.supervisor = SimpleNamespace(
        get_status=lambda: {
            'audio_process_alive': True,
        }
    )
    app.state.audio = SimpleNamespace(get_metrics=lambda limit=1: [{'timestamp': 1.0}])

    from app.api import routes as api_routes

    with monkeypatch.context() as m:
        # Older than max_age_seconds=30
        m.setattr(
            api_routes,
            '_is_recent_status_timestamp',
            lambda value, max_age_seconds: False,
        )
        response = TestClient(app).get('/api/runtime/media-status')

    assert response.status_code == 200
    payload = response.json()
    assert payload['audio_metrics_available'] is True
    assert payload['audio_online'] is False
