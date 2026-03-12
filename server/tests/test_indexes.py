from __future__ import annotations

from sqlmodel import SQLModel

import app.db.models  # noqa: F401


def _index_names(table_name: str) -> set[str]:
    table = SQLModel.metadata.tables[table_name]
    return {index.name for index in table.indexes}


def test_image_audio_command_indexes_present() -> None:
    assert "idx_image_timestamp" in _index_names("image_data")
    assert "idx_audio_timestamp" in _index_names("audio_data")
    assert "idx_command_timestamp" in _index_names("command_logs")
