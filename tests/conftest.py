"""Shared pytest fixtures."""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.api import export_routes
from src.api import generation_runtime
from src.config import reset_settings_manager
from src.api.schemas import HealthCheckItem, StartupHealthSummary
from src.database import Base, get_db, utc_now
from src.health_checks import DiskSpaceSnapshot
from src.main import app


@pytest.fixture(autouse=True)
def isolated_settings_manager() -> Generator[None, None, None]:
    """Reset the global settings manager before and after each test."""

    reset_settings_manager()
    try:
        yield
    finally:
        reset_settings_manager()


@pytest.fixture(autouse=True)
def isolated_generation_runtime() -> Generator[None, None, None]:
    """Reset process-local generation singletons around each test."""

    export_routes._clear_export_tasks()
    export_routes._shutdown_export_workers(timeout_seconds=0.1, recreate_executor=True)
    export_routes._batch_export_monitor_task = None
    export_routes._batch_export_progress = None
    export_routes._batch_export_history = {}
    generation_runtime.release_model_manager()
    generation_runtime._generator = None
    generation_runtime._queue = None
    generation_runtime._resource_monitor = None
    generation_runtime._batch_orchestrator = None
    try:
        yield
    finally:
        for task in export_routes._clear_export_tasks():
            task.cancel()
        export_routes._shutdown_export_workers(timeout_seconds=0.5, recreate_executor=True)
        if export_routes._batch_export_monitor_task is not None:
            export_routes._batch_export_monitor_task.cancel()
        export_routes._batch_export_monitor_task = None
        export_routes._batch_export_progress = None
        export_routes._batch_export_history = {}
        generation_runtime.release_model_manager()
        generation_runtime._generator = None
        generation_runtime._queue = None
        generation_runtime._resource_monitor = None
        generation_runtime._batch_orchestrator = None


@pytest.fixture(autouse=True)
def disable_sleep_prevention(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep queue tests from spawning platform-specific sleep-prevention processes."""

    monkeypatch.setattr("src.pipeline.queue_manager.prevent_sleep", lambda: None)
    monkeypatch.setattr("src.pipeline.queue_manager.allow_sleep", lambda: None)


@pytest.fixture(autouse=True)
def stable_disk_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep app startup deterministic regardless of the host machine's free disk space."""

    snapshot = DiskSpaceSnapshot(
        total_bytes=100 * (1024**3),
        used_bytes=50 * (1024**3),
        free_bytes=50 * (1024**3),
        percent_used=50.0,
    )
    monkeypatch.setattr("src.health_checks.get_disk_space_snapshot", lambda output_dir=None: snapshot)
    monkeypatch.setattr("src.main.get_disk_space_snapshot", lambda output_dir=None: snapshot)


@pytest.fixture(scope="function")
def test_db() -> Generator[Session, None, None]:
    """Create an isolated in-memory database session for tests."""

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    session = session_factory()

    try:
        yield session
    finally:
        session.close()


@pytest.fixture(scope="function")
def client(test_db: Session, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    """Create an application test client."""

    def override_get_db() -> Generator[Session, None, None]:
        yield test_db

    async def fake_health_checks() -> StartupHealthSummary:
        checks = [
            HealthCheckItem(name="Database Connection", status="pass", detail="ok", critical=True),
            HealthCheckItem(name="Model Files", status="pass", detail="ok", critical=True),
            HealthCheckItem(name="mlx-audio Import", status="pass", detail="ok", critical=True),
            HealthCheckItem(name="ffmpeg", status="pass", detail="ok", critical=True),
            HealthCheckItem(name="Manuscript Folder", status="pass", detail="ok", critical=False),
            HealthCheckItem(name="Output Directory", status="pass", detail="ok", critical=True),
            HealthCheckItem(name="Disk Space", status="pass", detail="ok", critical=False),
            HealthCheckItem(name="Empty Python Files", status="pass", detail="ok", critical=False),
        ]
        return StartupHealthSummary(checked_at=utc_now(), checks=checks, warnings=[], errors=[])

    async def fake_start_generation_runtime(*, resume_pending: bool = False) -> None:
        del resume_pending

    monkeypatch.setattr("src.main.init_db", lambda: None)
    monkeypatch.setattr("src.main.run_startup_cleanup", lambda: (0, 0))
    monkeypatch.setattr("src.main.run_export_startup_cleanup", lambda: (0, 0))
    monkeypatch.setattr("src.main.start_generation_runtime", fake_start_generation_runtime)
    monkeypatch.setattr("src.main.install_signal_handlers", lambda: None)
    monkeypatch.setattr("src.main.run_all_health_checks", fake_health_checks)

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
