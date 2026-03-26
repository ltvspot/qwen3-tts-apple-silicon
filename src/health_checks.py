"""Startup health checks and structured health reporting."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text

from src.api.schemas import HealthCheckItem, StartupHealthSummary
from src.config import get_application_settings, settings
from src.database import engine, utc_now
from src.notifications import send_disk_warning_notification

logger = logging.getLogger(__name__)


class HealthCheckError(RuntimeError):
    """Raised when a startup health check fails."""


class HealthCheckWarning(HealthCheckError):
    """Raised when a startup health check should degrade service without aborting startup."""


@dataclass(slots=True)
class DiskSpaceSnapshot:
    """Point-in-time disk usage for the configured output volume."""

    total_bytes: int
    used_bytes: int
    free_bytes: int
    percent_used: float

    @property
    def total_gb(self) -> int:
        """Return total space rounded down to whole GB."""

        return self.total_bytes // (1024**3)

    @property
    def free_gb(self) -> int:
        """Return free space rounded down to whole GB."""

        return self.free_bytes // (1024**3)


@dataclass(slots=True)
class RegisteredHealthCheck:
    """Configuration for one startup health check."""

    name: str
    check_func: Callable[[], Awaitable[None]]
    fatal: bool
    failure_status: str = "fail"


def get_disk_space_snapshot(output_dir: str | Path | None = None) -> DiskSpaceSnapshot:
    """Return disk usage for the configured output volume."""

    target_dir = Path(output_dir or settings.OUTPUTS_PATH)
    target_dir.mkdir(parents=True, exist_ok=True)
    disk_usage = shutil.disk_usage(target_dir)
    percent_used = (disk_usage.used / disk_usage.total) * 100 if disk_usage.total else 0.0
    return DiskSpaceSnapshot(
        total_bytes=disk_usage.total,
        used_bytes=disk_usage.used,
        free_bytes=disk_usage.free,
        percent_used=round(percent_used, 1),
    )


def find_empty_python_files(root_dir: str | Path | None = None) -> list[str]:
    """Return repo-relative empty Python files outside virtualenvs and caches."""

    project_root = Path(root_dir or Path(__file__).resolve().parent.parent)
    empty_files: list[str] = []
    excluded_dirs = {
        ".git",
        ".hg",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        ".venv-py314-backup",
        "__pycache__",
        "node_modules",
    }

    for current_root, dirs, files in os.walk(project_root):
        dirs[:] = [
            directory
            for directory in dirs
            if directory not in excluded_dirs and not directory.startswith(".venv")
        ]
        for filename in files:
            if not filename.endswith(".py"):
                continue
            file_path = Path(current_root) / filename
            if file_path.stat().st_size == 0:
                empty_files.append(str(file_path.relative_to(project_root)))

    return sorted(empty_files)


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


async def check_disk_space() -> None:
    """Verify that the output volume has enough remaining headroom."""

    snapshot = get_disk_space_snapshot()
    detail = f"{snapshot.percent_used:.1f}% used, {snapshot.free_gb}GB free"

    if snapshot.percent_used > 95:
        send_disk_warning_notification(
            free_gb=float(snapshot.free_gb),
            percent_used=snapshot.percent_used,
            critical=True,
        )
        raise HealthCheckError(f"CRITICAL: {detail}")
    if snapshot.percent_used > 90:
        send_disk_warning_notification(
            free_gb=float(snapshot.free_gb),
            percent_used=snapshot.percent_used,
            critical=False,
        )
        raise HealthCheckWarning(f"WARNING: {detail}")


async def check_file_integrity() -> None:
    """Detect empty Python files, which indicate the known repo corruption bug."""

    empty_files = find_empty_python_files()
    if not empty_files:
        return

    sample = ", ".join(empty_files[:5])
    suffix = "" if len(empty_files) <= 5 else ", ..."
    raise HealthCheckError(
        f"CORRUPTED: {len(empty_files)} empty .py files detected: {sample}{suffix}"
    )


async def run_all_health_checks() -> StartupHealthSummary:
    """
    Run all startup health checks and return a structured summary.

    Database, model availability, and output writability are treated as critical.
    ffmpeg and manuscript path checks degrade gracefully with warnings.
    """

    checks = [
        RegisteredHealthCheck("Database Connection", check_database_connection, True),
        RegisteredHealthCheck("TTS Model Files", check_model_files_exist, True),
        RegisteredHealthCheck("ffmpeg Installation", check_ffmpeg_installed, False, "warn"),
        RegisteredHealthCheck("Manuscript Folder", check_manuscript_folder_exists, False, "warn"),
        RegisteredHealthCheck("Output Directory", check_output_directory_writable, True),
        RegisteredHealthCheck("Disk Space", check_disk_space, False, "fail"),
        RegisteredHealthCheck("File Integrity", check_file_integrity, True),
    ]

    results: list[HealthCheckItem] = []
    warnings: list[str] = []
    errors: list[str] = []

    logger.info("Running startup health checks")

    for registered_check in checks:
        try:
            await registered_check.check_func()
        except HealthCheckWarning as exc:
            results.append(
                HealthCheckItem(
                    name=registered_check.name,
                    status="warn",
                    detail=str(exc),
                    critical=False,
                )
            )
            warnings.append(f"{registered_check.name}: {exc}")
            logger.warning("Health check warning: %s", exc)
        except HealthCheckError as exc:
            status = registered_check.failure_status
            results.append(
                HealthCheckItem(
                    name=registered_check.name,
                    status=status,
                    detail=str(exc),
                    critical=registered_check.fatal and status == "fail",
                )
            )
            if registered_check.fatal:
                errors.append(f"{registered_check.name}: {exc}")
                logger.error("Health check failed: %s", exc)
            else:
                warnings.append(f"{registered_check.name}: {exc}")
                logger.warning("Health check warning: %s", exc)
        else:
            results.append(
                HealthCheckItem(
                    name=registered_check.name,
                    status="pass",
                    detail="OK",
                    critical=registered_check.fatal,
                )
            )
            logger.info("Health check passed: %s", registered_check.name)

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
