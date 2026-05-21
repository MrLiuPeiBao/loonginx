from __future__ import annotations

from types import SimpleNamespace


def test_build_threshold_overrides() -> None:
    from app.services.config_sync import build_threshold_overrides

    payload = build_threshold_overrides(
        [
            SimpleNamespace(type="co", min_threshold=0, max_threshold=35),
            SimpleNamespace(type="o2", min_threshold=19.5, max_threshold=23.5),
        ]
    )

    assert payload == {
        "LOCAL_SENSOR_THRESHOLDS": {
            "co": {"min": 0.0, "max": 35.0},
            "o2": {"min": 19.5, "max": 23.5},
        }
    }
