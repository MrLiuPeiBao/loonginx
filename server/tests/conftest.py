"""Test fixtures for isolated SQLModel sessions."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

from app.db import models  # noqa: F401


@pytest.fixture()
def session() -> Iterator[Session]:
    """Provide an in-memory SQLite session for each test case."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
