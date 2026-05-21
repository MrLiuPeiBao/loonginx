"""ASGI entrypoint."""

from __future__ import annotations

from app.api import create_app


app = create_app()

