"""Rotating file log for the monitor.

setup_logging() attaches one RotatingFileHandler (1 MB x 3, UTF-8) to the usage_monitor
logger. It is idempotent and never raises: when the file cannot be opened, the logger gets
a NullHandler and the app runs without a log file.
"""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT_NAME = "usage_monitor"
FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
MAX_BYTES = 1024 * 1024
BACKUP_COUNT = 3


class _FileHandler(RotatingFileHandler):
    """The handler setup_logging installs. Its own type lets a later call find and replace it."""


class _NullHandler(logging.NullHandler):
    """Stands in for the file handler when the log file cannot be opened."""


def get_logger(name: str) -> logging.Logger:
    """Logger for ``name``, nested under usage_monitor. Pass ``__name__`` from package modules."""
    if name == ROOT_NAME or name.startswith(ROOT_NAME + "."):
        return logging.getLogger(name)
    return logging.getLogger(f"{ROOT_NAME}.{name}")


def setup_logging(level: int | str = logging.INFO, path: str | Path | None = None) -> None:
    """Send usage_monitor logs to ``path`` (default: paths.log_path()). Never raises.

    Repeating the call for the same file only updates the level. Naming another file closes
    the old handler and switches over.
    """
    logger = logging.getLogger(ROOT_NAME)
    try:
        logger.setLevel(level)
    except (TypeError, ValueError):
        logger.setLevel(logging.INFO)

    target = _resolve_target(path)
    for handler in _managed_handlers(logger):
        if target and _is_same_file_handler(handler, target):
            return
        logger.removeHandler(handler)
        handler.close()
    logger.addHandler(_open_handler(target))


def mirror_logger(name: str) -> None:
    """Also send the library logger ``name`` to the file log setup_logging opened.

    pywebview logs as "pywebview", outside the usage_monitor tree, so its warnings (an unusable renderer, a web view
    that failed to start) never reached the log file. Repeating the call is harmless.
    """
    target = logging.getLogger(name)
    for handler in _managed_handlers(logging.getLogger(ROOT_NAME)):
        if handler not in target.handlers:
            target.addHandler(handler)
    if target.level == logging.NOTSET:
        target.setLevel(logging.INFO)


def _managed_handlers(logger: logging.Logger) -> list[logging.Handler]:
    return [h for h in logger.handlers if isinstance(h, (_FileHandler, _NullHandler))]


def _is_same_file_handler(handler: logging.Handler, target: str) -> bool:
    if not isinstance(handler, _FileHandler):
        return False
    return os.path.normcase(handler.baseFilename) == os.path.normcase(target)


def _resolve_target(path: str | Path | None) -> str | None:
    try:
        if path is None:
            # Imported late because paths imports this module for its logger; a top-level
            # import here would be circular.
            from usage_monitor.paths import log_path

            path = log_path()
        return os.path.abspath(path)
    except Exception as exc:  # locating the log file must never stop startup
        logging.getLogger(ROOT_NAME).warning("log file location unavailable: %s", exc)
        return None


def _open_handler(target: str | None) -> logging.Handler:
    if target is None:
        return _NullHandler()
    try:
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        handler = _FileHandler(
            target, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
        )
    except (OSError, ValueError) as exc:
        logging.getLogger(ROOT_NAME).warning(
            "file logging disabled, cannot open %s: %s", target, exc
        )
        return _NullHandler()
    handler.setFormatter(logging.Formatter(FORMAT))
    return handler
