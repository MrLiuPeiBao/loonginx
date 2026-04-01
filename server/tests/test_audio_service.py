"""Audio service behavior tests."""

from __future__ import annotations

import json

from app.core.config import Settings
from app.services.audio_service import AudioMonitorService


def test_get_metrics_falls_back_to_persisted_snapshot(tmp_path) -> None:
    """Process-mode API instances should be able to read worker-persisted metrics."""
    service = AudioMonitorService(Settings())
    service._metrics_path = tmp_path / 'audio_metrics.json'
    service._metrics_path.write_text(
        json.dumps(
            [
                {'timestamp': 1.0, 'rms': 0.12},
                {'timestamp': 2.0, 'rms': 0.34},
            ]
        ),
        encoding='utf-8',
    )

    assert service.get_metrics(limit=1) == [{'timestamp': 2.0, 'rms': 0.34}]
