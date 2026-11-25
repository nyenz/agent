"""Log file setup. Full tracebacks go here and only here, never into the UI."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import app_data_dir

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def log_dir() -> Path:
    """Return the logs folder (created on demand)."""
    path = app_data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def setup_logging() -> Path:
    """Attach a rotating file handler (5 files x 1 MB) to the root logger; return the log path."""
    path = log_dir() / "fanout.log"
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in root.handlers:
        if isinstance(handler, RotatingFileHandler) and Path(handler.baseFilename) == path.resolve():
            return path
    handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root.addHandler(handler)
    return path
