"""Audio threshold ORM model."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class AudioThreshold(SQLModel, table=True):
    __tablename__ = 'audio_thresholds'

    id: Optional[int] = Field(default=None, primary_key=True)
    updated_at: datetime = Field(default_factory=datetime.now, nullable=False)
    centroid: float = Field(default=0.0, nullable=False)
    bandwidth: float = Field(default=0.0, nullable=False)
    rolloff: float = Field(default=0.0, nullable=False)
    flatness: float = Field(default=0.0, nullable=False)
    flux: float = Field(default=0.0, nullable=False)
    rms: float = Field(default=0.0, nullable=False)

