"""Pydantic compatibility tests."""

from __future__ import annotations

import importlib

import pytest


def test_settings_imports_with_pydantic_v2() -> None:
    try:
        config = importlib.import_module("app.core.config")
    except Exception as exc:  # noqa: BLE001 - convert import errors into test failures
        pytest.fail(f"config import failed: {exc}")
    assert hasattr(config, "Settings")
