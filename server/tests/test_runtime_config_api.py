"""Runtime config API tests."""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _build_client(session) -> TestClient:
    os.environ["APP_TEST_MODE"] = "1"
    from app.api.routes import router
    from app.db.session import get_session

    app = FastAPI()

    def _override_get_session():
        yield session

    app.dependency_overrides[get_session] = _override_get_session
    app.include_router(router)
    return TestClient(app)


def test_get_runtime_config(session) -> None:
    client = _build_client(session)
    resp = client.get("/api/runtime-config")
    assert resp.status_code == 200
    assert "config" in resp.json()
