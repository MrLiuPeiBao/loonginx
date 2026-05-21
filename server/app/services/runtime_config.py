from __future__ import annotations

import json
from typing import Dict

from app.db.models import RuntimeConfig


def load_runtime_overrides(session) -> Dict[str, object]:
    row = session.query(RuntimeConfig).order_by(RuntimeConfig.updated_at.desc()).first()
    if row is None:
        return {}
    try:
        data = json.loads(row.config_json or '{}')
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def save_runtime_overrides(session, overrides: Dict[str, object]) -> RuntimeConfig:
    row = RuntimeConfig(config_json=json.dumps(dict(overrides or {}), ensure_ascii=False))
    session.add(row)
    session.commit()
    session.refresh(row)
    return row

