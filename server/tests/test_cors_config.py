from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from app.api.routes import router
from app.core.config import Settings


def test_cors_origins_accept_comma_separated_env(monkeypatch):
    monkeypatch.setenv(
        'CORS_ALLOW_ORIGINS',
        'http://127.0.0.1:8000,http://localhost:8000',
    )

    settings = Settings(_env_file=None)

    assert settings.cors_allow_origins == [
        'http://127.0.0.1:8000',
        'http://localhost:8000',
    ]


def test_local_api_origin_is_effectively_allowed_when_api_binds_all_interfaces():
    settings = Settings(_env_file=None, api_host='0.0.0.0', api_port=8000, cors_allow_origins=[])

    assert 'http://127.0.0.1:8000' in settings.effective_cors_allow_origins
    assert 'http://localhost:8000' in settings.effective_cors_allow_origins


def test_cors_preflight_allows_local_api_origin():
    settings = Settings(_env_file=None, api_host='0.0.0.0', api_port=8000, cors_allow_origins=[])
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.effective_cors_allow_origins,
        allow_credentials=True,
        allow_methods=['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS', 'PATCH'],
        allow_headers=['*'],
    )

    @app.get('/api/health')
    def health() -> dict[str, str]:
        return {'status': 'ok'}

    response = TestClient(app).options(
        '/api/health',
        headers={
            'Origin': 'http://127.0.0.1:8000',
            'Access-Control-Request-Method': 'GET',
        },
    )

    assert response.status_code == 200
    assert response.headers['access-control-allow-origin'] == 'http://127.0.0.1:8000'


def test_runtime_config_prefers_same_origin_api_base_when_served_via_8888(monkeypatch):
    app = FastAPI()
    app.include_router(router)
    app.state.read_db_worker = SimpleNamespace(call_async=lambda func, *args, **kwargs: {})
    app.state.write_db_worker = SimpleNamespace(call_async=lambda func, *args, **kwargs: {})
    app.state.db_worker = app.state.write_db_worker

    async def fake_call_async(func, *args, **kwargs):
        return {'API_BASE_URL': 'http://localhost:8000'}

    app.state.read_db_worker.call_async = fake_call_async
    monkeypatch.setattr('app.api.routes.get_settings', lambda: Settings(_env_file=None))
    monkeypatch.setattr('app.api.routes.load_env_entries', lambda _: [])

    response = TestClient(app).get(
        '/api/runtime-config',
        headers={'host': '127.0.0.1:8888'},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload['config']['API_BASE_URL'] == 'http://127.0.0.1:8888'
