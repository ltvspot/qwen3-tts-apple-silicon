"""Tests for production logging configuration."""

from __future__ import annotations

import logging
from pathlib import Path

from src.logging_config import configure_logging


def _flush_managed_handlers() -> None:
    """Flush managed handlers so assertions can read the latest log contents."""

    for logger_name in ("", "api_requests", "src.pipeline"):
        logger = logging.getLogger(logger_name)
        for handler in logger.handlers:
            flush = getattr(handler, "flush", None)
            if callable(flush):
                flush()


def test_configure_logging_writes_expected_log_streams(tmp_path: Path) -> None:
    """Application, API, generation, and error logs should be written separately."""

    configure_logging(log_dir=tmp_path, max_bytes=2048)

    app_logger = logging.getLogger("tests.logging")
    api_logger = logging.getLogger("api_requests")
    generation_logger = logging.getLogger("src.pipeline.generator")

    app_logger.info("application log message")
    api_logger.info("api request log message")
    generation_logger.info("generation log message")

    try:
        raise RuntimeError("exploded")
    except RuntimeError:
        app_logger.exception("captured exception")

    _flush_managed_handlers()

    assert "application log message" in (tmp_path / "app.log").read_text(encoding="utf-8")
    assert "api request log message" in (tmp_path / "api.log").read_text(encoding="utf-8")
    assert "generation log message" in (tmp_path / "generation.log").read_text(encoding="utf-8")

    error_log = (tmp_path / "error.log").read_text(encoding="utf-8")
    assert "captured exception" in error_log
    assert "Traceback" in error_log


def test_configure_logging_rotates_app_log_when_size_limit_is_exceeded(tmp_path: Path) -> None:
    """Rotating file handlers should roll app logs when the file grows too large."""

    configure_logging(log_dir=tmp_path, max_bytes=256)

    logger = logging.getLogger("tests.rotation")
    for _ in range(24):
        logger.info("rotation line %s", "x" * 48)

    _flush_managed_handlers()

    rotated_logs = [path for path in tmp_path.iterdir() if path.name.startswith("app.log.")]
    assert rotated_logs
