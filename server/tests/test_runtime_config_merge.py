"""Runtime config merge tests."""

from __future__ import annotations

from app.core.config import merge_runtime_overrides


def test_merge_runtime_config_overrides_defaults() -> None:
    base = {"DATA_RETENTION_DAYS": 30, "COMMAND_TIMEOUT_SECONDS": 15}
    overrides = {"COMMAND_TIMEOUT_SECONDS": 60}
    merged = merge_runtime_overrides(base, overrides)
    assert merged["DATA_RETENTION_DAYS"] == 30
    assert merged["COMMAND_TIMEOUT_SECONDS"] == 60
