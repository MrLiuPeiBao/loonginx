"""Config override tests."""

from __future__ import annotations


def test_override_merges_env_defaults(tmp_path) -> None:
    from utils.config_override import merge_overrides

    base = {"SENSOR_POLL_DELAY": 0.1}
    overrides = {"SENSOR_POLL_DELAY": 0.5}
    assert merge_overrides(base, overrides)["SENSOR_POLL_DELAY"] == 0.5
