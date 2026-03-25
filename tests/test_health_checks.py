"""Tests for startup health-check helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from src import health_checks
from src.health_checks import HealthCheckError


@pytest.mark.asyncio
async def test_check_ffmpeg_installed_reports_install_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing ffmpeg should produce an actionable installation hint."""

    def raise_missing_ffmpeg(*args, **kwargs):
        del args, kwargs
        raise FileNotFoundError

    monkeypatch.setattr(health_checks.subprocess, "run", raise_missing_ffmpeg)

    with pytest.raises(HealthCheckError, match="brew install ffmpeg"):
        await health_checks.check_ffmpeg_installed()


@pytest.mark.asyncio
async def test_check_output_directory_writable_reports_permission_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Unwritable output directories should fail clearly."""

    monkeypatch.setattr(health_checks.settings, "OUTPUTS_PATH", str(tmp_path / "outputs"))

    def raise_permission_error(self, data, encoding=None, errors=None, newline=None):
        del self, data, encoding, errors, newline
        raise PermissionError("read only")

    monkeypatch.setattr(Path, "write_text", raise_permission_error)

    with pytest.raises(HealthCheckError, match="Output directory not writable"):
        await health_checks.check_output_directory_writable()


@pytest.mark.asyncio
async def test_check_output_directory_writable_ignores_cleanup_failures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Cleanup errors should not fail an otherwise successful writability check."""

    monkeypatch.setattr(health_checks.settings, "OUTPUTS_PATH", str(tmp_path / "outputs"))

    def raise_cleanup_error(self, missing_ok=False):
        del self, missing_ok
        raise PermissionError("cleanup denied")

    monkeypatch.setattr(Path, "unlink", raise_cleanup_error)

    await health_checks.check_output_directory_writable()


@pytest.mark.asyncio
async def test_run_all_health_checks_returns_warning_summary_for_noncritical_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Warning-only failures should degrade health without aborting startup."""

    async def pass_check() -> None:
        return None

    async def ffmpeg_warning() -> None:
        raise HealthCheckError("ffmpeg missing")

    async def manuscript_warning() -> None:
        raise HealthCheckError("manuscripts missing")

    monkeypatch.setattr(health_checks, "check_database_connection", pass_check)
    monkeypatch.setattr(health_checks, "check_model_files_exist", pass_check)
    monkeypatch.setattr(health_checks, "check_ffmpeg_installed", ffmpeg_warning)
    monkeypatch.setattr(health_checks, "check_manuscript_folder_exists", manuscript_warning)
    monkeypatch.setattr(health_checks, "check_output_directory_writable", pass_check)

    summary = await health_checks.run_all_health_checks()

    assert summary.errors == []
    assert summary.warnings == [
        "ffmpeg Installation: ffmpeg missing",
        "Manuscript Folder: manuscripts missing",
    ]
    assert [check.status for check in summary.checks] == ["pass", "pass", "warn", "warn", "pass"]


@pytest.mark.asyncio
async def test_run_all_health_checks_raises_on_critical_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    """Critical check failures should abort startup with detailed context."""

    async def failing_database() -> None:
        raise HealthCheckError("database unavailable")

    async def pass_check() -> None:
        return None

    monkeypatch.setattr(health_checks, "check_database_connection", failing_database)
    monkeypatch.setattr(health_checks, "check_model_files_exist", pass_check)
    monkeypatch.setattr(health_checks, "check_ffmpeg_installed", pass_check)
    monkeypatch.setattr(health_checks, "check_manuscript_folder_exists", pass_check)
    monkeypatch.setattr(health_checks, "check_output_directory_writable", pass_check)

    with pytest.raises(HealthCheckError, match="Database Connection: database unavailable"):
        await health_checks.run_all_health_checks()
