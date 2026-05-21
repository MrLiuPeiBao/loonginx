from __future__ import annotations

from sqlalchemy import create_engine

from app.db import session as db_session


def test_db_ping_returns_false_for_driver_errors(monkeypatch):
    class BrokenEngine:
        def connect(self):
            raise RuntimeError("driver auth dependency missing")

    monkeypatch.setattr(db_session, "engine", BrokenEngine())

    assert db_session.db_ping() is False


def test_register_sql_observers_is_idempotent():
    engine = create_engine("sqlite://")

    db_session._register_sql_observers(engine)
    before = list(engine.dispatch.before_cursor_execute)
    after = list(engine.dispatch.after_cursor_execute)
    db_session._register_sql_observers(engine)

    assert list(engine.dispatch.before_cursor_execute) == before
    assert list(engine.dispatch.after_cursor_execute) == after
