from __future__ import annotations

from pathlib import Path
from typing import Dict, List


def serialize_env_value(value: object) -> str:
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if value is None:
        return ''
    return str(value)


def load_env_entries(path: Path) -> List[Dict[str, str]]:
    entries: List[Dict[str, str]] = []
    if not path.is_file():
        return entries
    pending_comment: list[str] = []
    for raw_line in path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line:
            pending_comment = []
            continue
        if line.startswith('#'):
            pending_comment.append(line.lstrip('#').strip())
            continue
        if '=' not in raw_line:
            continue
        key, value = raw_line.split('=', 1)
        entries.append(
            {
                'key': key.strip(),
                'value': value.strip(),
                'comment': '\n'.join(pending_comment),
            }
        )
        pending_comment = []
    return entries


def update_env_file(path: Path, changes: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding='utf-8').splitlines() if path.is_file() else []
    seen: set[str] = set()
    output: list[str] = []
    for line in existing:
        if '=' not in line or line.lstrip().startswith('#'):
            output.append(line)
            continue
        key, _value = line.split('=', 1)
        normalized_key = key.strip()
        if normalized_key in changes:
            output.append(f'{normalized_key}={serialize_env_value(changes[normalized_key])}')
            seen.add(normalized_key)
        else:
            output.append(line)
    for key, value in changes.items():
        if key not in seen:
            output.append(f'{key}={serialize_env_value(value)}')
    path.write_text('\n'.join(output) + '\n', encoding='utf-8')

