"""A copy of the log in the data volume.

Docker keeps the console log with the container, and every deploy recreates the container: the log of the morning was gone
after the evening deploy of 09-11-2026, with an experiment running whose only evidence was in it. This file lives in the data
volume (`logs/evbro.log`), survives deploys and restarts, and rotates so it never grows without limit.
"""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from typing import Optional

LOG_FILE_MAX_BYTES = 5 * 1024 * 1024      # a few months of a normal install
LOG_FILE_BACKUPS = 4
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

_LOGGER = logging.getLogger(__name__)


def log_file_path(data_dir: str) -> str:
    return os.path.join(data_dir, "logs", "evbro.log")


def install_file_logging(data_dir: str) -> Optional[str]:
    """Adds the rotating file to the root logger and returns its path; None if it cannot be written (the console log goes on).

    Idempotent: a second call with the same folder does nothing, and one with another folder replaces the previous file."""
    path = log_file_path(data_dir)
    root = logging.getLogger()
    for handler in list(root.handlers):
        mark = getattr(handler, "_evbro_log_file", None)
        if mark == path:
            return path
        if mark is not None:
            root.removeHandler(handler)
            handler.close()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        handler = RotatingFileHandler(path, maxBytes=LOG_FILE_MAX_BYTES, backupCount=LOG_FILE_BACKUPS, encoding="utf-8")
    except OSError as exc:
        _LOGGER.warning("The log file could not be opened at %s: %s", path, exc)
        return None
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler._evbro_log_file = path
    root.addHandler(handler)
    return path
