from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RtspCaptureConfig:
    input_url: str
    buffer_size: int = 1
    capture_options: str = ''


def open_capture(config: RtspCaptureConfig):
    try:
        import cv2
    except Exception:
        return None
    cap = cv2.VideoCapture(str(config.input_url or ''))
    try:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, max(0, int(config.buffer_size or 0)))
    except Exception:
        pass
    return cap


def calc_drop_frames(elapsed_seconds: float, target_fps: int, max_drop_frames: int) -> int:
    frame_interval = 1.0 / float(max(1, int(target_fps or 1)))
    overrun = max(0.0, float(elapsed_seconds or 0.0) - frame_interval)
    return min(max(0, int(max_drop_frames or 0)), int(overrun / frame_interval))


def drain_capture(cap, count: int) -> int:
    drained = 0
    for _ in range(max(0, int(count or 0))):
        try:
            ok = cap.grab()
        except Exception:
            break
        if not ok:
            break
        drained += 1
    return drained

