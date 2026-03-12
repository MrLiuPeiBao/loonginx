"""数据库会话管理。"""

from __future__ import annotations

import logging
import os
import threading
from contextlib import contextmanager
from typing import Iterator, Set

from sqlalchemy import inspect
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, SQLModel, create_engine
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.db.audio_thresholds import AudioThreshold


logger = logging.getLogger(__name__)

def _build_engine():
    if os.getenv('APP_TEST_MODE'):
        return create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    settings = get_settings()
    return create_engine(
        settings.mysql_url,
        echo=False,
        pool_pre_ping=True,
        connect_args={'connect_timeout': 5},
    )


_engine_lock = threading.Lock()
engine = _build_engine()


def rebuild_engine() -> None:
    """Rebuild SQLAlchemy engine with latest settings."""
    global engine
    with _engine_lock:
        old_engine = engine
        engine = _build_engine()
        try:
            old_engine.dispose()
        except Exception:
            pass


def init_db() -> None:
    """初始化数据库表结构，并修复缺失列。"""
    SQLModel.metadata.create_all(engine)
    AudioThreshold.__table__.create(bind=engine, checkfirst=True)
    _ensure_alarm_table_schema()
    _ensure_image_table_schema()
    _ensure_audio_table_schema()
    _ensure_runtime_config_schema()


def db_ping() -> bool:
    """Check whether database connection is available.

    Returns:
        bool: True when DB responds to a simple query, otherwise False.
    """
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql('SELECT 1')
        return True
    except SQLAlchemyError:
        return False


def get_session() -> Iterator[Session]:
    """FastAPI 依赖使用的 Session 生成器。"""
    with Session(engine) as session:
        yield session


@contextmanager
def session_scope() -> Iterator[Session]:
    """同步代码块的事务上下文。"""
    with Session(engine) as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


def _ensure_alarm_table_schema() -> None:
    _ensure_alarm_location_column()
    _ensure_alarm_type_enum()


def _ensure_image_table_schema() -> None:
    """Ensure image_data table owns required columns."""
    try:
        inspector = inspect(engine)
        columns: Set[str] = {col['name'] for col in inspector.get_columns('image_data')}
    except Exception:  # pragma: no cover - 依赖数据库
        logger.exception('Failed to inspect image_data table')
        return
    if 'location' not in columns:
        ddl = "ALTER TABLE image_data ADD COLUMN location VARCHAR(255) NOT NULL DEFAULT ''"
        try:
            with engine.begin() as connection:
                connection.exec_driver_sql(ddl)
            logger.info('Added missing location column to image_data table')
        except Exception:  # pragma: no cover - 依赖数据库
            logger.exception('Failed to add location column to image_data')
    if 'image_path' not in columns:
        ddl = "ALTER TABLE image_data ADD COLUMN image_path VARCHAR(512) NULL"
        try:
            with engine.begin() as connection:
                connection.exec_driver_sql(ddl)
            logger.info('Added missing image_path column to image_data table')
        except Exception:  # pragma: no cover - 依赖数据库
            logger.exception('Failed to add image_path column to image_data')


def _ensure_audio_table_schema() -> None:
    """Ensure audio_data table has location column and sufficient BLOB storage type."""
    try:
        inspector = inspect(engine)
        columns: Set[str] = {col['name'] for col in inspector.get_columns('audio_data')}
        column_info = {col['name']: col for col in inspector.get_columns('audio_data')}
    except Exception:  # pragma: no cover - 依赖数据源
        logger.exception('Failed to inspect audio_data table')
        return

    if 'location' not in columns:
        ddl = "ALTER TABLE audio_data ADD COLUMN location VARCHAR(255) NOT NULL DEFAULT ''"
        try:
            with engine.begin() as connection:
                connection.exec_driver_sql(ddl)
            logger.info('Added missing location column to audio_data table')
        except Exception:  # pragma: no cover - 依赖数据源
            logger.exception('Failed to add location column to audio_data')
    if 'audio_path' not in columns:
        ddl = "ALTER TABLE audio_data ADD COLUMN audio_path VARCHAR(512) NULL"
        try:
            with engine.begin() as connection:
                connection.exec_driver_sql(ddl)
            logger.info('Added missing audio_path column to audio_data table')
        except Exception:  # pragma: no cover - 依赖数据源
            logger.exception('Failed to add audio_path column to audio_data')

    # Ensure audio_data column can hold raw WAV bytes (upgrade to LONGBLOB).
    col = column_info.get('audio_data')
    if col:
        type_str = str(col.get('type') or '').lower()
        if 'longblob' not in type_str:
            try:
                with engine.begin() as connection:
                    connection.exec_driver_sql("ALTER TABLE audio_data MODIFY audio_data LONGBLOB NOT NULL")
                logger.info('Expanded audio_data column to LONGBLOB')
            except Exception:  # pragma: no cover - 依赖数据源
                logger.exception('Failed to expand audio_data column to LONGBLOB')


def _ensure_runtime_config_schema() -> None:
    # Ensure runtime_config.config_json column is large enough.
    try:
        inspector = inspect(engine)
        columns = {col['name']: col for col in inspector.get_columns('runtime_config')}
    except Exception:  # pragma: no cover - ????????
        logger.exception('Failed to inspect runtime_config table')
        return

    column = columns.get('config_json')
    if not column:
        return
    type_str = str(column.get('type') or '').lower()
    if 'longtext' in type_str:
        return
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("ALTER TABLE runtime_config MODIFY config_json LONGTEXT NOT NULL")
        logger.info('Expanded runtime_config.config_json to LONGTEXT')
    except Exception:  # pragma: no cover - ????????
        logger.exception('Failed to expand runtime_config.config_json column')


def _ensure_alarm_location_column() -> None:
    """确保 alarm_records 表存在 location 列。"""
    try:
        inspector = inspect(engine)
        columns: Set[str] = {col['name'] for col in inspector.get_columns('alarm_records')}
    except Exception:  # pragma: no cover - 依赖数据库
        logger.exception('Failed to inspect alarm_records table')
        return

    if 'location' in columns:
        return

    ddl = "ALTER TABLE alarm_records ADD COLUMN location VARCHAR(255) NOT NULL DEFAULT ''"
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(ddl)
        logger.info('Added missing location column to alarm_records table')
    except Exception:  # pragma: no cover - 依赖数据库
        logger.exception('Failed to add location column to alarm_records')


def _ensure_alarm_type_enum() -> None:
    """确保 alarm_type 字段使用小写枚举值。"""
    try:
        with engine.begin() as connection:
            result = connection.exec_driver_sql(
                "SHOW COLUMNS FROM alarm_records LIKE 'alarm_type'"
            )
            column = result.mappings().first()
    except Exception:  # pragma: no cover - 依赖数据库
        logger.exception('Failed to inspect alarm_type column')
        return

    if not column:
        return

    column_type = (column['Type'] or '').strip().lower()
    if column_type == "enum('low','high')":
        return

    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "ALTER TABLE alarm_records MODIFY alarm_type ENUM('low','high','LOW','HIGH') NOT NULL"
            )
            connection.exec_driver_sql(
                "UPDATE alarm_records SET alarm_type=LOWER(alarm_type) WHERE alarm_type IS NOT NULL"
            )
            connection.exec_driver_sql(
                "ALTER TABLE alarm_records MODIFY alarm_type ENUM('low','high') NOT NULL"
            )
        logger.info('Normalized alarm_type enum to lowercase values')
    except Exception:  # pragma: no cover - 依赖数据库
        logger.exception('Failed to normalize alarm_type enum')
