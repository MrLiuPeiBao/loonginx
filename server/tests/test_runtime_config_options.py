"""Runtime config options tests."""

from __future__ import annotations


def test_config_options_has_sections() -> None:
    from app.gui.config_options import CONFIG_SECTIONS

    assert "data_retention" in CONFIG_SECTIONS
