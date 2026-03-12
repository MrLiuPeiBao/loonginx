"""Query limit defaults tests."""

from __future__ import annotations

import os
from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db.models import SensorData


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


def test_list_sensor_data_default_limit(session) -> None:
    now = datetime.now()
    old = now - timedelta(days=2)
    session.add(
        SensorData(
            timestamp=old,
            device_id="dev-old",
            location="lab",
        )
    )
    session.add(
        SensorData(
            timestamp=now,
            device_id="dev-new",
            location="lab",
        )
    )
    session.commit()

    client = _build_client(session)
    resp = client.get("/api/sensors")
    assert resp.status_code == 200
    payload = resp.json()
    assert any(item["device_id"] == "dev-new" for item in payload)
    assert all(item["device_id"] != "dev-old" for item in payload)
