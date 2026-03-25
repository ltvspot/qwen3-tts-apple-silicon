"""Centralized logging configuration for the application."""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

DEFAULT_LOG_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_APP_BACKUP_COUNT = 5
DEFAULT_STREAM_BACKUP_COUNT = 3
MANAGED_HANDLER_NAMES = {
    "alexandria-app-file",
    "alexandria-console",
    "alexandria-root-error-file",
    "alexandria-api-file",
    "alexandria-generation-file",
}


def configure_logging(
    *,
    level: str = "INFO",
    log_dir: str | Path = "logs",
    max_bytes: int = DEFAULT_LOG_MAX_BYTES,
) -> Path:
    """
    Configure rotating file logging for the application.

    Returns:
        The resolved log directory used for log output.
    """

    resolved_log_dir = Path(log_dir)
    resolved_log_dir.mkdir(parents=True, exist_ok=True)

    log_level = getattr(logging, level.upper(), logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    _remove_managed_handlers(root_logger)
    _add_handler(
        root_logger,
        _rotating_handler(
            resolved_log_dir / "app.log",
            formatter,
            level=log_level,
            max_bytes=max_bytes,
            backup_count=DEFAULT_APP_BACKUP_COUNT,
            name="alexandria-app-file",
        ),
    )
    _add_handler(
        root_logger,
        _stream_handler(
            formatter,
            level=log_level,
            name="alexandria-console",
        ),
    )
    _add_handler(
        root_logger,
        _rotating_handler(
            resolved_log_dir / "error.log",
            formatter,
            level=logging.ERROR,
            max_bytes=max_bytes,
            backup_count=DEFAULT_APP_BACKUP_COUNT,
            name="alexandria-root-error-file",
        ),
    )

    api_logger = logging.getLogger("api_requests")
    api_logger.setLevel(log_level)
    api_logger.propagate = False
    _remove_managed_handlers(api_logger)
    _add_handler(
        api_logger,
        _rotating_handler(
            resolved_log_dir / "api.log",
            formatter,
            level=log_level,
            max_bytes=max_bytes,
            backup_count=DEFAULT_STREAM_BACKUP_COUNT,
            name="alexandria-api-file",
        ),
    )

    generation_logger = logging.getLogger("src.pipeline")
    generation_logger.setLevel(log_level)
    generation_logger.propagate = True
    _remove_managed_handlers(generation_logger)
    _add_handler(
        generation_logger,
        _rotating_handler(
            resolved_log_dir / "generation.log",
            formatter,
            level=log_level,
            max_bytes=max_bytes,
            backup_count=DEFAULT_STREAM_BACKUP_COUNT,
            name="alexandria-generation-file",
        ),
    )

    logging.getLogger(__name__).info("Logging configured in %s", resolved_log_dir)
    return resolved_log_dir


def _remove_managed_handlers(logger: logging.Logger) -> None:
    """Remove previously configured Alexandria handlers from a logger."""

    for handler in list(logger.handlers):
        if getattr(handler, "name", None) in MANAGED_HANDLER_NAMES:
            logger.removeHandler(handler)
            handler.close()


def _add_handler(logger: logging.Logger, handler: logging.Handler) -> None:
    """Attach a handler to a logger."""

    logger.addHandler(handler)


def _rotating_handler(
    path: Path,
    formatter: logging.Formatter,
    *,
    level: int,
    max_bytes: int,
    backup_count: int,
    name: str,
) -> logging.Handler:
    """Create a rotating file handler."""

    handler = logging.handlers.RotatingFileHandler(
        path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    handler.name = name
    handler.setLevel(level)
    handler.setFormatter(formatter)
    return handler


def _stream_handler(
    formatter: logging.Formatter,
    *,
    level: int,
    name: str,
) -> logging.Handler:
    """Create a console stream handler."""

    handler = logging.StreamHandler()
    handler.name = name
    handler.setLevel(level)
    handler.setFormatter(formatter)
    return handler
