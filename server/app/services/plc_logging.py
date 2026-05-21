from __future__ import annotations

import logging
from pathlib import Path


_LOGGER_NAME = "plc"
_CONFIG_FLAG = "_plc_logging_configured"


def _configure_plc_logger(log_file: Path) -> logging.Logger:
    logger = logging.getLogger(_LOGGER_NAME)
    if getattr(logger, _CONFIG_FLAG, False):
        return logger

    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    formatter = logging.Formatter("[PLC] %(asctime)s %(levelname)s %(name)s: %(message)s")

    log_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        file_handler.setLevel(logging.DEBUG)
        logger.addHandler(file_handler)
    except OSError as exc:
        logger.warning("Failed to open PLC log file %s: %s", log_file, exc)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    stream_handler.setLevel(logging.INFO)
    logger.addHandler(stream_handler)

    setattr(logger, _CONFIG_FLAG, True)
    return logger


def get_plc_logger(name: str) -> logging.Logger:
    base_dir = Path(__file__).resolve().parents[2]
    log_file = base_dir / "logs" / "plc.log"
    _configure_plc_logger(log_file)
    return logging.getLogger(f"{_LOGGER_NAME}.{name}")
