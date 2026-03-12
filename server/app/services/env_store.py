"""Environment file persistence helpers."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Tuple

_ENV_KEY_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


def serialize_env_value(value: object) -> str:
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if value is None:
        return ''
    if isinstance(value, (list, tuple, set)):
        return ','.join(str(item).strip() for item in value if str(item).strip())
    return str(value)


def _parse_env_line(line: str) -> Tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith('#'):
        return None
    if '=' not in stripped:
        return None
    key, value = stripped.split('=', 1)
    key = key.strip()
    if not _ENV_KEY_RE.match(key):
        return None
    return key, value.strip()


def load_env_entries(path: Path) -> List[Dict[str, str]]:
    """Load env entries with optional description comments."""
    if not path.exists():
        return []
    entries: List[Dict[str, str]] = []
    pending_comments: list[str] = []
    for line in path.read_text(encoding='utf-8').splitlines():
        stripped = line.strip()
        if not stripped:
            pending_comments = []
            continue
        if stripped.startswith('#'):
            comment = stripped.lstrip('#').strip()
            if comment:
                pending_comments.append(comment)
            continue
        parsed = _parse_env_line(line)
        if not parsed:
            pending_comments = []
            continue
        key, value = parsed
        entries.append(
            {
                'key': key,
                'value': value,
                'comment': ' '.join(pending_comments).strip(),
            }
        )
        pending_comments = []
    return entries


def load_env_file(path: Path) -> Dict[str, str]:
    data: Dict[str, str] = {}
    for entry in load_env_entries(path):
        data[entry['key']] = entry['value']
    return data


def update_env_file(path: Path, updates: Dict[str, object]) -> None:
    lines = []
    seen = set()
    if path.exists():
        lines = path.read_text(encoding='utf-8').splitlines()
    out_lines = []
    for line in lines:
        parsed = _parse_env_line(line)
        if not parsed:
            out_lines.append(line)
            continue
        key, _value = parsed
        if key in updates:
            out_lines.append(f"{key}={serialize_env_value(updates[key])}")
            seen.add(key)
        else:
            out_lines.append(line)
    for key, value in updates.items():
        if key in seen:
            continue
        if not _ENV_KEY_RE.match(key):
            continue
        out_lines.append(f"{key}={serialize_env_value(value)}")
    content = '\n'.join(out_lines).rstrip('\n') + '\n'
    path.write_text(content, encoding='utf-8')
