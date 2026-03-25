"""Startup health checks and structured health reporting."""

from __future__ import annotations

import asyncio
import logging
import subprocess
from pathlib import Path

from sqlalchemy import text

from src.api.schemas import HealthCheckItem, StartupHealthSummary
from src.config import get_application_settings, settings
from src.database import engine, utc_now

logger = logging.getLogger(__name__)


class HealthCheckError(RuntimeError):
    """Raised when a startup health check fails."""


async def check_database_connection() -> None:
    """Verify that the configured database can be queried."""

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - exercised through tests
        raise HealthCheckError(f"Database connection failed: {exc}") from exc


async def check_model_files_exist() -> None:
    """Verify that the configured TTS model path exists."""

    application_settings = get_application_settings()
    model_path = Path(application_settings.engine_config.model_path)
    if not model_path.exists():
        raise HealthCheckError(
            f"TTS model not found: {model_path}. Download models or update Settings.engine_config.model_path."
        )


async def check_ffmpeg_installed() -> None:
    """Verify that ffmpeg is available for export-related operations."""

    try:
        result = await asyncio.to_thread(
            subprocess.run,
            ["ffmpeg", "-version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except FileNotFoundError as exc:
        raise HealthCheckError("ffmpeg not found. Install with `brew install ffmpeg`.") from exc
    except Exception as exc:  # pragma: no cover - exercised through tests
        raise HealthCheckError(f"ffmpeg check failed: {exc}") from exc

    if result.returncode != 0:
        raise HealthCheckError("ffmpeg is installed but not working correctly.")


async def check_manuscript_folder_exists() -> None:
    """Verify that the persisted manuscript source folder exists."""

    application_settings = get_application_settings()
    manuscript_folder = Path(application_settings.manuscript_source_folder)
    if not manuscript_folder.exists():
        raise HealthCheckError(
            f"Manuscript folder not found: {manuscript_folder}. Configure manuscript_source_folder in Settings."
        )


async def check_output_directory_writable() -> None:
    """Verify that the configured output directory is writable."""

    output_dir = Path(settings.OUTPUTS_PATH)
    output_dir.mkdir(parents=True, exist_ok=True)

    test_file = output_dir / ".write_test"
    try:
        test_file.write_text("ok", encoding="utf-8")
    except Exception as exc:  # pragma: no cover - exercised through tests
        raise HealthCheckError(f"Output directory not writable: {output_dir} ({exc})") from exc
    finally:
        try:
            test_file.unlink(missing_ok=True)
        except (OSError, PermissionError):
            pass


async def run_all_health_checks() -> StartupHealthSummary:
    """
    Run all startup health checks and return a structured summary.

    Database, model availability, and output writability are treated as critical.
    ffmpeg and manuscript path checks degrade gracefully with warnings.
    """

    checks = [
        ("Database Connection", check_database_connection, True),
        ("TTS Model Files", check_model_files_exist, True),
        ("ffmpeg Installation", check_ffmpeg_installed, False),
        ("Manuscript Folder", check_manuscript_folder_exists, False),
        ("Output Directory", check_output_directory_writable, True),
    ]

    results: list[HealthCheckItem] = []
    warnings: list[str] = []
    errors: list[str] = []

    logger.info("Running startup health checks")

    for name, check_func, critical in checks:
        try:
            await check_func()
        except HealthCheckError as exc:
            status = "fail" if critical else "warn"
            results.append(HealthCheckItem(name=name, status=status, detail=str(exc), critical=critical))
            if critical:
                errors.append(f"{name}: {exc}")
                logger.error("Health check failed: %s", exc)
            else:
                warnings.append(f"{name}: {exc}")
                logger.warning("Health check warning: %s", exc)
        else:
            results.append(HealthCheckItem(name=name, status="pass", detail="OK", critical=critical))
            logger.info("Health check passed: %s", name)

    summary = StartupHealthSummary(
        checked_at=utc_now(),
        checks=results,
        warnings=warnings,
        errors=errors,
    )

    if errors:
        error = HealthCheckError(
            "Critical startup health checks failed: " + "; ".join(errors)
        )
        setattr(error, "summary", summary)
        raise error

    return summary
