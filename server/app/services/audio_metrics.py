"""Audio feature extraction helpers (best-effort).

This module intentionally keeps heavy dependencies optional at runtime, so the
server can still run (and at least rely on RMS/peak) even if librosa/numpy are
unavailable.
"""

from __future__ import annotations

import logging
import wave
from typing import Dict

logger = logging.getLogger(__name__)


def compute_spectral_metrics(wav_path: str) -> Dict[str, float]:
    """Compute spectral features from a WAV file using librosa.

    Args:
        wav_path (str): Local WAV file path (PCM16 recommended).

    Returns:
        Dict[str, float]: Spectral metrics dict with keys:
            - centroid
            - bandwidth
            - rolloff
            - flatness
            - flux
            - rms
        Returns an empty dict when computation is not possible.
    """
    try:
        import numpy as np  # type: ignore
        import librosa  # type: ignore
    except Exception as exc:
        logger.warning('librosa/numpy unavailable for spectral metrics: %s', exc)
        return {}

    try:
        with wave.open(wav_path, 'rb') as wf:
            channels = int(wf.getnchannels())
            sample_width = int(wf.getsampwidth())
            sample_rate = int(wf.getframerate())
            frames = wf.readframes(int(wf.getnframes()))
    except Exception as exc:
        logger.warning('Failed to read wav for spectral metrics: %s', exc)
        return {}

    if not frames or sample_width != 2 or sample_rate <= 0:
        return {}

    try:
        audio = np.frombuffer(frames, dtype='<i2').astype(np.float32) / 32768.0
        if channels > 1:
            audio = audio.reshape((-1, channels)).mean(axis=1)
        if audio.size == 0:
            return {}

        centroid = float(librosa.feature.spectral_centroid(y=audio, sr=sample_rate).mean())
        bandwidth = float(librosa.feature.spectral_bandwidth(y=audio, sr=sample_rate).mean())
        rolloff = float(
            librosa.feature.spectral_rolloff(y=audio, sr=sample_rate, roll_percent=0.85).mean()
        )
        flatness = float(librosa.feature.spectral_flatness(y=audio).mean())
        flux = float(librosa.onset.onset_strength(y=audio, sr=sample_rate).mean())
        rms = float(librosa.feature.rms(y=audio).mean())
        return {
            'centroid': centroid,
            'bandwidth': bandwidth,
            'rolloff': rolloff,
            'flatness': flatness,
            'flux': flux,
            'rms': rms,
        }
    except Exception as exc:
        logger.warning('Failed to compute spectral metrics: %s', exc)
        return {}

