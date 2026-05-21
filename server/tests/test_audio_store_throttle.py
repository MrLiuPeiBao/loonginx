from __future__ import annotations

import wave

from app.core.config import Settings
from app.ipc import IPCUnavailableError
from app.services.audio_service import AudioMonitorService, _next_rtsp_failure_backoff


def test_audio_store_interval_throttles_repeated_writes():
    settings = Settings(_env_file=None, audio_store_min_interval_seconds=10.0)
    service = AudioMonitorService(settings)

    assert service._store_interval_allows() is True
    assert service._store_interval_allows() is False


def test_audio_store_interval_can_be_disabled():
    settings = Settings(_env_file=None, audio_store_min_interval_seconds=0.0)
    service = AudioMonitorService(settings)

    assert service._store_interval_allows() is True
    assert service._store_interval_allows() is True


def test_audio_rtsp_failure_backoff_is_bounded():
    first = _next_rtsp_failure_backoff(
        0.0,
        initial_seconds=10.0,
        max_seconds=30.0,
    )
    second = _next_rtsp_failure_backoff(
        first,
        initial_seconds=10.0,
        max_seconds=30.0,
    )
    capped = _next_rtsp_failure_backoff(
        second,
        initial_seconds=10.0,
        max_seconds=30.0,
    )

    assert first == 10.0
    assert second == 20.0
    assert capped == 30.0


def test_audio_store_skips_when_telemetry_pipe_unavailable(tmp_path):
    class FailingTelemetry:
        calls = 0

        def emit(self, **kwargs):
            self.calls += 1
            raise IPCUnavailableError('missing pipe')

    wav_path = tmp_path / 'sample.wav'
    with wave.open(str(wav_path), 'wb') as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(b'\x00\x00' * 16)

    telemetry = FailingTelemetry()
    service = AudioMonitorService(Settings(_env_file=None), telemetry_client=telemetry)

    service._store_wav_file(str(wav_path), {'wav_rms': 0.0})

    assert telemetry.calls == 1
