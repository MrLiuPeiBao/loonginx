"""运行期配置覆盖服务。"""

from __future__ import annotations

import json
from datetime import datetime

from sqlmodel import Session, select

from app.db.models import RuntimeConfig


def get_runtime_config_row(session: Session) -> RuntimeConfig | None:
    return session.exec(select(RuntimeConfig).order_by(RuntimeConfig.id.desc())).first()


def load_runtime_overrides(session: Session) -> dict:
    row = get_runtime_config_row(session)
    if not row or not row.config_json:
        return {}
    try:
        data = json.loads(row.config_json)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def save_runtime_overrides(session: Session, overrides: dict) -> RuntimeConfig:
    payload = json.dumps(overrides or {}, ensure_ascii=False)
    row = get_runtime_config_row(session)
    if row is None:
        row = RuntimeConfig(config_json=payload)
        session.add(row)
    else:
        row.config_json = payload
        row.updated_at = datetime.now()
    session.commit()
    session.refresh(row)
    return row
