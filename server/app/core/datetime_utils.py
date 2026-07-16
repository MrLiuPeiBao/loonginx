"""日期时间解析与格式化工具函数。"""

from __future__ import annotations

from datetime import datetime
from typing import Any


def to_local_naive(value: datetime) -> datetime:
    """将带时区的 datetime 转换为本地无时区 datetime。"""
    if value.tzinfo is None:
        return value
    return value.astimezone().replace(tzinfo=None)


def parse_datetime(value: Any) -> datetime:
    """将多种格式的输入解析为无时区 datetime。"""
    if isinstance(value, datetime):
        return to_local_naive(value)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value))
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            return to_local_naive(parsed)
        except ValueError:
            pass
    return datetime.now()
