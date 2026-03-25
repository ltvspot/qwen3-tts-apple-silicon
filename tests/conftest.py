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
from src.database import Base, get_db
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

    export_routes._export_tasks.clear()
    export_routes._batch_export_monitor_task = None
    export_routes._batch_export_progress = None
    generation_runtime.release_model_manager()
    generation_runtime._generator = None
    generation_runtime._queue = None
    generation_runtime._resource_monitor = None
    generation_runtime._batch_orchestrator = None
    try:
        yield
    finally:
        for task in list(export_routes._export_tasks):
            task.cancel()
        export_routes._export_tasks.clear()
        if export_routes._batch_export_monitor_task is not None:
            export_routes._batch_export_monitor_task.cancel()
        export_routes._batch_export_monitor_task = None
        export_routes._batch_export_progress = None
        generation_runtime.release_model_manager()
        generation_runtime._generator = None
        generation_runtime._queue = None
        generation_runtime._resource_monitor = None
        generation_runtime._batch_orchestrator = None


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
def client(test_db: Session) -> Generator[TestClient, None, None]:
    """Create an application test client."""

    def override_get_db() -> Generator[Session, None, None]:
        yield test_db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
