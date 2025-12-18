"""Audio threshold persistence."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class AudioThreshold(SQLModel, table=True):
    """Persisted audio threshold configuration."""

    __tablename__ = 'audio_thresholds'

    id: Optional[int] = Field(default=None, primary_key=True)
    updated_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    centroid: float = Field(default=0.0)
    bandwidth: float = Field(default=0.0)
    rolloff: float = Field(default=0.0)
    flatness: float = Field(default=0.0)
    flux: float = Field(default=0.0)
    rms: float = Field(default=0.0)
