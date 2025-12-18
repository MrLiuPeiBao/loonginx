"""Audio recording service for RTSP streams.

Implementation goal: simplest, most reliable persistence path.
- Use ffmpeg to pull RTSP audio and write a WAV file (PCM16).
- Compute metrics per window and store audio into DB only when thresholds are exceeded.
"""

from __future__ import annotations

import logging
import math
import os
import subprocess
import sys
import tempfile
import threading
import time
import wave
from array import array
from datetime import datetime
from typing import Deque, Dict, Optional
from collections import deque
from urllib.parse import urlsplit, urlunsplit

from sqlmodel import select

from app.core.config import Settings
from app.db.audio_thresholds import AudioThreshold
from app.db.models import AudioData
from app.db.session import session_scope
from app.mqtt import MQTTManager
from app.services.audio_metrics import compute_spectral_metrics
from app.services.data_service import DataService
from app.services.alarm_publisher import build_alarm_event, publish_alarm_event

logger = logging.getLogger(__name__)


def _ensure_console_logging() -> None:
    """Ensure audio logs are always visible in console (even under uvicorn log filters)."""
    marker = '_audio_console_handler'
    for handler in logger.handlers:
        if getattr(handler, marker, False):
            return
    console_handler = logging.StreamHandler(stream=sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter('[AUDIO] %(asctime)s %(levelname)s: %(message)s'))
    setattr(console_handler, marker, True)
    logger.addHandler(console_handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


_ensure_console_logging()


def _redact_rtsp_url(url: str) -> str:
    """Redact credentials in RTSP URL for safer logging."""
    if not url:
        return url
    try:
        parts = urlsplit(url)
        if parts.username is None or parts.password is None:
            return url
        hostname = parts.hostname or ''
        if parts.port:
            hostname = f'{hostname}:{parts.port}'
        netloc = f'{parts.username}:***@{hostname}'
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    except Exception:
        return url


def _safe_filename_part(text: str, *, max_len: int = 40) -> str:
    """Convert arbitrary text into a filesystem-safe short token."""
    cleaned = ''.join(ch if ch.isalnum() or ch in {'-', '_'} else '_' for ch in (text or ''))
    cleaned = cleaned.strip('_') or 'device'
    return cleaned[:max_len]


class AudioMonitorService:
    """Continuously record RTSP audio and persist into database."""

    def __init__(self, settings: Settings, mqtt_manager: Optional[MQTTManager] = None):
        self._settings = settings
        self._mqtt = mqtt_manager
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._metrics: Deque[Dict[str, float]] = deque(maxlen=200)
        self._last_window: Optional[Dict[str, float]] = None
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self._settings.audio_enabled and self._settings.audio_rtsp_input)

    def start(self) -> None:
        if not self.enabled:
            logger.warning(
                'Audio monitor not started: AUDIO_ENABLED=%s AUDIO_RTSP_INPUT_set=%s',
                bool(self._settings.audio_enabled),
                bool(self._settings.audio_rtsp_input),
            )
            return
        self._load_latest_thresholds()
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name='audio-monitor', daemon=True)
        self._thread.start()
        logger.warning('Audio monitor service started')

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        logger.warning('Audio monitor service stopped')

    # Internal -------------------------------------------------------------
    def _run(self) -> None:
        sample_rate = int(self._settings.audio_sample_rate or 16000)
        segment_seconds = max(0.5, float(self._settings.audio_window_seconds or 1.0))
        url = (self._settings.audio_rtsp_input or '').strip()
        url_for_log = _redact_rtsp_url(url)
        logger.warning(
            'Audio recording loop started: url=%s sample_rate=%s segment_seconds=%.2f',
            url_for_log,
            sample_rate,
            segment_seconds,
        )

        while not self._stop_event.is_set():
            tmp_path = self._allocate_temp_wav_path()
            try:
                capture_start = time.perf_counter()
                if not self._capture_segment_to_wav(
                    url=url,
                    sample_rate=sample_rate,
                    duration_seconds=segment_seconds,
                    output_path=tmp_path,
                ):
                    self._wait(2.0)
                    continue
                capture_elapsed = time.perf_counter() - capture_start

                file_size = self._safe_file_size(tmp_path)
                metrics: Dict[str, float] = {
                    'timestamp': time.time(),
                    'sample_rate': float(sample_rate),
                    'duration_seconds': float(segment_seconds),
                    'file_size_bytes': float(file_size),
                    'capture_elapsed_seconds': float(capture_elapsed),
                }
                metrics.update(self._inspect_wav_file(tmp_path))
                thresholds = self._get_thresholds()
                evaluation = self._evaluate_thresholds(metrics, thresholds, wav_path=tmp_path)
                should_store = bool(evaluation.get('should_store'))
                exceeded = evaluation.get('exceeded') or []
                metrics['threshold_hit'] = 1.0 if should_store else 0.0
                with self._lock:
                    self._metrics.append(metrics)
                    self._last_window = metrics

                logger.warning(
                    'Audio window captured: path=%s size_bytes=%s elapsed=%.3fs wav_duration=%.3fs wav_rms=%.1f peak=%.1f store=%s exceeded=%s',
                    tmp_path,
                    file_size,
                    capture_elapsed,
                    metrics.get('wav_duration_seconds', 0.0),
                    metrics.get('wav_rms', 0.0),
                    metrics.get('wav_peak', 0.0),
                    should_store,
                    exceeded,
                )
                if should_store:
                    logger.warning('Audio threshold hit: exceeded=%s thresholds=%s', exceeded, thresholds)
                    self._store_wav_file(tmp_path, metrics)
                else:
                    logger.info('Audio window skipped (below thresholds): thresholds=%s', thresholds)
            except Exception:  # pragma: no cover - background thread safety
                logger.exception('Audio recording loop crashed, retrying')
                self._wait(2.0)
            finally:
                self._cleanup_file(tmp_path)

    def _allocate_temp_wav_path(self) -> str:
        """Allocate a temp path for ffmpeg output wav."""
        fd, path = tempfile.mkstemp(prefix='rtsp_audio_', suffix='.wav')
        os.close(fd)
        return path

    def _capture_segment_to_wav(
        self,
        *,
        url: str,
        sample_rate: int,
        duration_seconds: float,
        output_path: str,
    ) -> bool:
        """Run ffmpeg to capture one WAV segment file."""
        if not url:
            logger.error('AUDIO_RTSP_INPUT is empty, cannot capture audio')
            return False
        cmd = [
            'ffmpeg',
            '-y',
            '-loglevel',
            'error',
            '-rtsp_transport',
            'tcp',
            '-rtsp_flags',
            'prefer_tcp',
            '-stimeout',
            '5000000',
            '-i',
            url,
            '-map',
            '0:a:0',
            '-vn',
            '-ac',
            '1',
            '-ar',
            str(sample_rate),
            '-c:a',
            'pcm_s16le',
            '-t',
            f'{duration_seconds:.3f}',
            '-f',
            'wav',
            output_path,
        ]
        timeout = max(10.0, float(duration_seconds) + 10.0)
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except FileNotFoundError:
            logger.error('ffmpeg not found, audio recording disabled')
            return False
        except subprocess.TimeoutExpired:
            logger.warning('ffmpeg capture timeout after %.1fs (duration=%.2fs)', timeout, duration_seconds)
            return False
        except Exception as exc:  # pragma: no cover
            logger.exception('ffmpeg capture failed: %s', exc)
            return False

        if result.returncode != 0:
            stderr = (result.stderr or '').strip().replace('\n', ' ')
            logger.warning('ffmpeg capture failed rc=%s stderr=%s', result.returncode, stderr[:300])
            return False
        if not os.path.exists(output_path):
            logger.warning('ffmpeg finished but output file missing: %s', output_path)
            return False
        if self._safe_file_size(output_path) <= 44:  # WAV header only
            logger.warning('ffmpeg output too small, likely no audio: %s', output_path)
            return False
        return True

    def _store_wav_file(self, wav_path: str, metrics: Dict[str, float]) -> None:
        """Read WAV file and store into database (raw WAV bytes)."""
        timestamp = datetime.utcnow()
        device_id = self._settings.audio_device_id or 'mic'
        location = self._settings.audio_location or 'rtsp'
        audio_name = f'audio_{_safe_filename_part(device_id)}_{timestamp.strftime("%Y%m%d_%H%M%S_%f")}.wav'

        try:
            with open(wav_path, 'rb') as f:
                wav_bytes = f.read()
            logger.warning(
                'WAV file loaded: bytes=%s metrics=%s',
                len(wav_bytes),
                metrics,
            )
        except Exception:  # pragma: no cover - codec errors
            logger.exception('Failed to read/encode wav file: %s', wav_path)
            return

        try:
            with session_scope() as session:
                service = DataService(session)
                latest_rfid = service.get_latest_rfid_card()
                if latest_rfid:
                    location = latest_rfid
                entity = AudioData(
                    timestamp=timestamp,
                    device_id=device_id,
                    audio_name=audio_name,
                    audio_data=wav_bytes,
                    location=location,
                )
                stored = service.create_audio_data([entity])
                stored_id = stored[0].id if stored else None
            logger.warning(
                'Audio snippet stored: id=%s name=%s device=%s location=%s bytes=%s',
                stored_id,
                audio_name,
                device_id,
                location,
                len(wav_bytes),
            )
            event = build_alarm_event(
                source='audio_threshold',
                timestamp=timestamp,
                device_id=device_id,
                location=location,
                payload={
                    'audio_id': stored_id,
                    'audio_name': audio_name,
                    'bytes': len(wav_bytes),
                    'wav_duration_seconds': float(metrics.get('wav_duration_seconds', 0.0)),
                    'wav_rms': float(metrics.get('wav_rms', 0.0)),
                    'wav_peak': float(metrics.get('wav_peak', 0.0)),
                    'capture_elapsed_seconds': float(metrics.get('capture_elapsed_seconds', 0.0)),
                    'file_size_bytes': float(metrics.get('file_size_bytes', 0.0)),
                    'threshold_hit': bool(metrics.get('threshold_hit', 0.0)),
                },
            )
            publish_alarm_event(self._mqtt, event)
        except Exception:  # pragma: no cover - DB dependency
            logger.exception('Failed to store audio snippet %s', audio_name)

    @staticmethod
    def _inspect_wav_file(path: str) -> Dict[str, float]:
        """Extract lightweight WAV metrics for logs/diagnostics."""
        try:
            with wave.open(path, 'rb') as wf:
                channels = int(wf.getnchannels())
                sample_width = int(wf.getsampwidth())
                frame_rate = int(wf.getframerate())
                n_frames = int(wf.getnframes())
                duration = float(n_frames) / float(frame_rate) if frame_rate else 0.0
                frames = wf.readframes(n_frames)
            rms = 0.0
            peak = 0.0
            if frames and sample_width == 2:
                samples = array('h')
                samples.frombytes(frames)
                if sys.byteorder != 'little':
                    samples.byteswap()
                if samples:
                    peak = float(max(abs(sample) for sample in samples))
                    mean_square = sum(int(sample) * int(sample) for sample in samples) / float(len(samples))
                    rms = float(math.sqrt(mean_square))
            return {
                'wav_channels': float(channels),
                'wav_sample_width_bytes': float(sample_width),
                'wav_frame_rate': float(frame_rate),
                'wav_frames': float(n_frames),
                'wav_duration_seconds': float(duration),
                'wav_rms': float(rms),
                'wav_peak': float(peak),
            }
        except Exception:
            logger.warning('Failed to inspect wav file: %s', path)
            return {}

    @staticmethod
    def _safe_file_size(path: str) -> int:
        try:
            return os.path.getsize(path)
        except OSError:
            return 0

    @staticmethod
    def _cleanup_file(path: str) -> None:
        if not path:
            return
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass

    def _wait(self, seconds: float) -> None:
        end = time.time() + seconds
        while not self._stop_event.is_set() and time.time() < end:
            time.sleep(0.2)

    # API helpers ---------------------------------------------------------
    def get_metrics(self, limit: int = 100) -> list[Dict[str, float]]:
        with self._lock:
            return list(self._metrics)[-limit:]

    def update_thresholds(self, **kwargs: float) -> None:
        """Update in-memory thresholds.

        Accepts either full setting keys (audio_threshold_*) or short keys
        (centroid/bandwidth/rolloff/flatness/flux/rms).
        """
        mapping = {
            'centroid': 'audio_threshold_centroid',
            'bandwidth': 'audio_threshold_bandwidth',
            'rolloff': 'audio_threshold_rolloff',
            'flatness': 'audio_threshold_flatness',
            'flux': 'audio_threshold_flux',
            'rms': 'audio_threshold_rms',
            'audio_threshold_centroid': 'audio_threshold_centroid',
            'audio_threshold_bandwidth': 'audio_threshold_bandwidth',
            'audio_threshold_rolloff': 'audio_threshold_rolloff',
            'audio_threshold_flatness': 'audio_threshold_flatness',
            'audio_threshold_flux': 'audio_threshold_flux',
            'audio_threshold_rms': 'audio_threshold_rms',
        }
        updated: Dict[str, float] = {}
        for incoming_key, target_attr in mapping.items():
            if incoming_key not in kwargs:
                continue
            try:
                value = float(kwargs[incoming_key])
            except Exception:
                continue
            setattr(self._settings, target_attr, value)
            updated[target_attr] = value
        if updated:
            logger.warning('Audio thresholds updated: %s', updated)

    def _load_latest_thresholds(self) -> None:
        """Load persisted thresholds from DB and apply to in-memory settings."""
        try:
            with session_scope() as session:
                latest = session.exec(
                    select(AudioThreshold)
                    .order_by(AudioThreshold.updated_at.desc())
                    .limit(1)
                ).first()
                if not latest:
                    return
                self.update_thresholds(
                    centroid=latest.centroid,
                    bandwidth=latest.bandwidth,
                    rolloff=latest.rolloff,
                    flatness=latest.flatness,
                    flux=latest.flux,
                    rms=latest.rms,
                )
                logger.warning(
                    'Loaded audio thresholds from DB: centroid=%s bandwidth=%s rolloff=%s flatness=%s flux=%s rms=%s',
                    latest.centroid,
                    latest.bandwidth,
                    latest.rolloff,
                    latest.flatness,
                    latest.flux,
                    latest.rms,
                )
        except Exception:  # pragma: no cover - DB dependency
            logger.exception('Failed to load audio thresholds from DB')

    def _get_thresholds(self) -> Dict[str, float]:
        """Snapshot current threshold settings."""
        return {
            'centroid': float(getattr(self._settings, 'audio_threshold_centroid', 0.0) or 0.0),
            'bandwidth': float(getattr(self._settings, 'audio_threshold_bandwidth', 0.0) or 0.0),
            'rolloff': float(getattr(self._settings, 'audio_threshold_rolloff', 0.0) or 0.0),
            'flatness': float(getattr(self._settings, 'audio_threshold_flatness', 0.0) or 0.0),
            'flux': float(getattr(self._settings, 'audio_threshold_flux', 0.0) or 0.0),
            'rms': float(getattr(self._settings, 'audio_threshold_rms', 0.0) or 0.0),
        }

    def _evaluate_thresholds(
        self,
        metrics: Dict[str, float],
        thresholds: Dict[str, float],
        *,
        wav_path: str,
    ) -> Dict[str, object]:
        """Decide whether to store the current window based on thresholds."""
        if bool(getattr(self._settings, 'audio_debug_store_all', False)):
            return {'should_store': True, 'exceeded': ['debug_store_all']}

        active_thresholds = {k: v for k, v in thresholds.items() if float(v) > 0.0}
        if not active_thresholds:
            # All thresholds == 0 => record everything.
            return {'should_store': True, 'exceeded': ['thresholds_all_zero']}

        exceeded: list[str] = []

        # RMS supports two scales:
        # - <= 1.0: normalized RMS (0..1)
        # - > 1.0: int16 RMS (0..32767), for user convenience
        threshold_rms = float(thresholds.get('rms', 0.0) or 0.0)
        if threshold_rms > 0.0:
            wav_rms = float(metrics.get('wav_rms', 0.0) or 0.0)
            rms_norm = wav_rms / 32768.0
            metrics['rms_norm'] = float(rms_norm)
            if threshold_rms <= 1.0:
                if rms_norm > threshold_rms:
                    exceeded.append('rms')
            else:
                if wav_rms > threshold_rms:
                    exceeded.append('rms')

        spectral_needed = any(
            float(thresholds.get(key, 0.0) or 0.0) > 0.0
            for key in ('centroid', 'bandwidth', 'rolloff', 'flatness', 'flux')
        )
        if spectral_needed:
            spectral = compute_spectral_metrics(wav_path)
            metrics.update(spectral)
            for key in ('centroid', 'bandwidth', 'rolloff', 'flatness', 'flux'):
                threshold = float(thresholds.get(key, 0.0) or 0.0)
                if threshold <= 0.0:
                    continue
                value = float(metrics.get(key, 0.0) or 0.0)
                if value > threshold:
                    exceeded.append(key)

        return {'should_store': bool(exceeded), 'exceeded': exceeded}
