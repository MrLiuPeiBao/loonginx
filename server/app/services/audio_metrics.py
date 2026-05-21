from __future__ import annotations

from typing import Dict


def compute_spectral_metrics(wav_path: str) -> Dict[str, float]:
    """Compute optional spectral metrics from a WAV file.

    The API must remain importable even when numpy/librosa are unavailable, so
    dependency failures return an empty metrics payload.
    """
    try:
        import librosa
        import numpy as np
    except Exception:
        return {}

    try:
        samples, sample_rate = librosa.load(wav_path, sr=None, mono=True)
        if samples is None or len(samples) == 0:
            return {}
        centroid = librosa.feature.spectral_centroid(y=samples, sr=sample_rate)
        bandwidth = librosa.feature.spectral_bandwidth(y=samples, sr=sample_rate)
        rolloff = librosa.feature.spectral_rolloff(y=samples, sr=sample_rate)
        flatness = librosa.feature.spectral_flatness(y=samples)
        flux = np.sqrt(np.mean(np.diff(samples) ** 2)) if len(samples) > 1 else 0.0
        return {
            'centroid': float(np.mean(centroid)),
            'bandwidth': float(np.mean(bandwidth)),
            'rolloff': float(np.mean(rolloff)),
            'flatness': float(np.mean(flatness)),
            'flux': float(flux),
        }
    except Exception:
        return {}

