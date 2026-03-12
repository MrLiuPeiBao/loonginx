"""Runtime config model tests."""

from __future__ import annotations

from sqlmodel import select

from app.db.models import RuntimeConfig


def test_runtime_config_create_and_update(session) -> None:
    cfg = RuntimeConfig(config_json='{"key": 1}')
    session.add(cfg)
    session.commit()

    row = session.exec(select(RuntimeConfig)).first()
    assert row is not None
    assert "key" in row.config_json
