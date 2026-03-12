"""Lightweight RTSP audio monitor for GUI-side real-time metrics."""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from typing import Dict, Optional

import numpy as np
import librosa

logger = logging.getLogger(__name__)


class AudioRTSPMonitor:
    """Read audio from RTSP via ffmpeg and expose latest spectral metrics."""

    def __init__(self, url: str, sample_rate: int = 16000, window_seconds: float = 1.0):
        self.url = url
        self.sample_rate = sample_rate
        self.window_seconds = max(0.5, window_seconds)
        self.samples_per_window = int(self.sample_rate * self.window_seconds)
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._ffmpeg: Optional[subprocess.Popen] = None
        self._latest: Optional[Dict[str, float]] = None
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name='audio-rtsp-monitor', daemon=True)
        self._thread.start()
        logger.info('GUI audio monitor started for %s', self.url)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        self._terminate_ffmpeg()

    def get_latest(self) -> Optional[Dict[str, float]]:
        with self._lock:
            return dict(self._latest) if self._latest else None

    # Internal -------------------------------------------------------------
    def _run(self) -> None:
        while not self._stop_event.is_set():
            proc = self._spawn_ffmpeg()
            if not proc:
                self._wait(5.0)
                continue
            self._ffmpeg = proc
            buffer = bytearray()
            try:
                while not self._stop_event.is_set():
                    chunk = proc.stdout.read(self.samples_per_window * 2)
                    if not chunk:
                        # If process still alive, wait for more data instead of restarting immediately.
                        if proc.poll() is None:
                            time.sleep(0.1)
                            continue
                        stderr_msg = ''
                        try:
                            stderr_msg = proc.stderr.read().decode(errors='ignore') if proc.stderr else ''
                        except Exception:
                            stderr_msg = ''
                        if stderr_msg:
                            logger.warning('GUI audio monitor stream ended, restarting (%s)', stderr_msg.strip())
                        else:
                            logger.warning('GUI audio monitor stream ended, restarting')
                        break
                    buffer.extend(chunk)
                    while len(buffer) >= self.samples_per_window * 2:
                        window = buffer[: self.samples_per_window * 2]
                        del buffer[: self.samples_per_window * 2]
                        self._process_window(window)
            except Exception:
                logger.exception('GUI audio monitor encountered an error')
            finally:
                self._terminate_ffmpeg()
                self._wait(1.0)

    def _process_window(self, raw_bytes: bytes) -> None:
        audio = np.frombuffer(raw_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        if audio.size == 0:
            return
        sr = self.sample_rate
        centroid = librosa.feature.spectral_centroid(y=audio, sr=sr).mean()
        bandwidth = librosa.feature.spectral_bandwidth(y=audio, sr=sr).mean()
        rolloff = librosa.feature.spectral_rolloff(y=audio, sr=sr, roll_percent=0.85).mean()
        flatness = librosa.feature.spectral_flatness(y=audio).mean()
        flux = librosa.onset.onset_strength(y=audio, sr=sr).mean()
        metrics = {
            'timestamp': time.time(),
            'centroid': float(centroid),
            'bandwidth': float(bandwidth),
            'rolloff': float(rolloff),
            'flatness': float(flatness),
            'flux': float(flux),
        }
        with self._lock:
            self._latest = metrics

    def _spawn_ffmpeg(self) -> Optional[subprocess.Popen]:
        cmd = [
            'ffmpeg',
            '-loglevel',
            'error',
            '-rtsp_transport',
            'tcp',
            '-rtsp_flags',
            'prefer_tcp',
            '-i',
            self.url,
            '-vn',
            '-ac',
            '1',
            '-ar',
            str(self.sample_rate),
            '-f',
            's16le',
            'pipe:1',
        ]
        try:
            return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except FileNotFoundError:
            logger.error('ffmpeg not found for GUI audio monitor')
        except Exception:
            logger.exception('Failed to start ffmpeg for GUI audio monitor')
        return None

    def _terminate_ffmpeg(self) -> None:
        proc = self._ffmpeg
        self._ffmpeg = None
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=1)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def _wait(self, seconds: float) -> None:
        end = time.time() + seconds
        while not self._stop_event.is_set() and time.time() < end:
            time.sleep(0.2)
