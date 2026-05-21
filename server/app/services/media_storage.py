"""Filesystem helpers for image/audio payload persistence."""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Optional


logger = logging.getLogger(__name__)


def _sanitize_name(name: str, *, fallback: str) -> str:
    raw = str(name or '').strip()
    if not raw:
        return fallback
    path_name = Path(raw).name
    cleaned = ''.join(ch for ch in path_name if ch.isalnum() or ch in {'.', '-', '_'})
    return cleaned or fallback


def _resolve_media_root(settings) -> Path:
    base = Path(__file__).resolve().parents[2]
    configured = str(getattr(settings, 'media_storage_dir', 'media') or 'media').strip()
    candidate = Path(configured)
    if not candidate.is_absolute():
        candidate = (base / candidate).resolve()
    return candidate


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def store_image_base64(settings, *, image_name: str, image_b64: str) -> Optional[str]:
    """Store image base64 payload on filesystem and return relative path."""
    text = str(image_b64 or '').strip()
    if not text:
        return None
    try:
        data = base64.b64decode(text, validate=False)
    except Exception:
        logger.warning('Invalid image base64 payload, skip file store')
        return None

    root = _resolve_media_root(settings)
    folder = root / 'images'
    _ensure_dir(folder)
    safe_name = _sanitize_name(image_name, fallback='image.bin')
    target = folder / safe_name
    if target.exists():
        stem = target.stem
        suffix = target.suffix
        for i in range(1, 10000):
            candidate = folder / f'{stem}_{i}{suffix}'
            if not candidate.exists():
                target = candidate
                break
    try:
        target.write_bytes(data)
    except Exception:
        logger.exception('Failed to store image file: %s', target)
        return None
    return target.relative_to(root).as_posix()


def store_audio_bytes(settings, *, audio_name: str, audio_bytes: bytes) -> Optional[str]:
    """Store audio bytes on filesystem and return relative path."""
    data = bytes(audio_bytes or b'')
    if not data:
        return None
    root = _resolve_media_root(settings)
    folder = root / 'audio'
    _ensure_dir(folder)
    safe_name = _sanitize_name(audio_name, fallback='audio.bin')
    target = folder / safe_name
    if target.exists():
        stem = target.stem
        suffix = target.suffix
        for i in range(1, 10000):
            candidate = folder / f'{stem}_{i}{suffix}'
            if not candidate.exists():
                target = candidate
                break
    try:
        target.write_bytes(data)
    except Exception:
        logger.exception('Failed to store audio file: %s', target)
        return None
    return target.relative_to(root).as_posix()


def load_file_base64(path: str, *, settings=None) -> Optional[str]:
    """Read file payload and return base64 string."""
    text = str(path or '').strip()
    if not text:
        return None
    candidate = Path(text)
    if not candidate.is_absolute():
        if settings is not None:
            root = _resolve_media_root(settings)
            candidate = root / candidate
        else:
            base = Path(__file__).resolve().parents[2]
            candidate = (base / 'media' / candidate).resolve()
    try:
        data = candidate.read_bytes()
    except Exception:
        return None
    if not data:
        return ''
    return base64.b64encode(data).decode('ascii')

