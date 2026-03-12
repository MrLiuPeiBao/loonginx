"""Optional filesystem storage for large media blobs."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Optional

from app.core.config import Settings


def _safe_name(name: str, *, max_len: int = 80) -> str:
    cleaned = ''.join(ch if ch.isalnum() or ch in {'-', '_', '.',} else '_' for ch in (name or 'media'))
    cleaned = cleaned.strip('_') or 'media'
    return cleaned[:max_len]


def _media_dir(settings: Settings, media_type: str) -> Path:
    base_dir = Path(settings.media_storage_dir or 'media')
    sub = 'images' if media_type == 'image' else 'audio'
    path = base_dir / sub
    path.mkdir(parents=True, exist_ok=True)
    return path


def store_image_base64(
    settings: Settings,
    *,
    image_name: str,
    image_b64: str,
) -> Optional[str]:
    if not image_b64:
        return None
    try:
        payload = base64.b64decode(image_b64, validate=False)
    except Exception:
        return None
    folder = _media_dir(settings, 'image')
    filename = _safe_name(image_name or 'image') or 'image'
    path = folder / filename
    try:
        path.write_bytes(payload)
    except OSError:
        return None
    return str(path)


def store_audio_bytes(
    settings: Settings,
    *,
    audio_name: str,
    audio_bytes: bytes,
) -> Optional[str]:
    if not audio_bytes:
        return None
    folder = _media_dir(settings, 'audio')
    filename = _safe_name(audio_name or 'audio') or 'audio'
    path = folder / filename
    try:
        path.write_bytes(audio_bytes)
    except OSError:
        return None
    return str(path)


def load_file_base64(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    return base64.b64encode(data).decode('ascii')
