"""RTSP capture helpers focused on low-latency streaming."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

try:  # pragma: no cover - optional dependency
    import cv2
except ImportError:  # pragma: no cover - optional dependency
    cv2 = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RtspCaptureConfig:
    """Configuration for RTSP capture."""

    input_url: str
    buffer_size: int
    capture_options: str = ''


def configure_capture_options(options: str) -> None:
    """Set OpenCV FFMPEG capture options if not already configured."""
    if not options:
        return
    if os.environ.get('OPENCV_FFMPEG_CAPTURE_OPTIONS'):
        return
    os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = options
    logger.info('Set OPENCV_FFMPEG_CAPTURE_OPTIONS=%s', options)


def open_capture(config: RtspCaptureConfig) -> Optional['cv2.VideoCapture']:
    """Open a VideoCapture with optional low-latency tuning."""
    if cv2 is None:
        return None

    options = (config.capture_options or '').strip()
    if options:
        configure_capture_options(options)

    backend = getattr(cv2, 'CAP_FFMPEG', 0)
    cap = cv2.VideoCapture(config.input_url, backend)
    if config.buffer_size > 0:
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, config.buffer_size)
        except Exception:  # pragma: no cover - backend specific
            logger.debug('Failed to set CAP_PROP_BUFFERSIZE', exc_info=True)
    return cap


def drain_capture(cap: 'cv2.VideoCapture', max_frames: int) -> int:
    """Grab and drop frames to reduce backlog."""
    if cv2 is None or max_frames <= 0:
        return 0
    drained = 0
    for _ in range(max_frames):
        if not cap.grab():
            break
        drained += 1
    return drained


def calc_drop_frames(process_seconds: float, target_fps: int, max_drop_frames: int) -> int:
    """Calculate how many frames to drop based on processing lag."""
    if target_fps <= 0 or max_drop_frames <= 0:
        return 0
    if process_seconds <= 0:
        return 0
    target_interval = 1.0 / float(target_fps)
    if process_seconds <= target_interval:
        return 0
    drop = int(process_seconds / target_interval) - 1
    return max(0, min(drop, max_drop_frames))
