"""Integration tests for persisted settings usage across the app."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

import src.config as config_module
from src.config import SettingsManager
from src.database import BookStatus, GenerationJob, GenerationJobStatus
from src.parser import CreditsGenerator
from src.pipeline.queue_manager import GenerationQueue
from tests.test_generation_pipeline import SlowGenerator, create_book, create_chapter
from src.database import ChapterType


def _manager(test_db: Session, config_file: Path) -> SettingsManager:
    """Create a settings manager bound to the isolated test database."""

    session_factory = sessionmaker(
        bind=test_db.get_bind(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    return SettingsManager(session_factory=session_factory, config_file=config_file)


def test_credits_generator_uses_persisted_narrator_name(
    test_db: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Generated credits should use the persisted narrator setting."""

    manager = _manager(test_db, tmp_path / "config.json")
    manager.update_settings({"narrator_name": "Alexandria Voice"})
    monkeypatch.setattr(config_module, "_settings_manager", manager)

    opening = CreditsGenerator.generate_opening_credits("Signal Fires", None, "Jane Doe")
    closing = CreditsGenerator.generate_closing_credits("Signal Fires", None, "Jane Doe")

    assert "Narrated by Alexandria Voice." in opening
    assert "Narrated by Alexandria Voice." in closing


@pytest.mark.asyncio
async def test_generation_queue_uses_persisted_default_voice_settings(
    test_db: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Queued generation should inherit the default voice settings when not explicitly supplied."""

    manager = _manager(test_db, tmp_path / "config.json")
    manager.update_settings(
        {
            "default_voice": {
                "name": "Nova",
                "emotion": "calm",
                "speed": 1.15,
            },
        }
    )
    monkeypatch.setattr(config_module, "_settings_manager", manager)

    book = create_book(test_db, title="Configured Voice Book", status=BookStatus.PARSED)
    create_chapter(
        test_db,
        book_id=book.id,
        number=1,
        title="Chapter One",
        chapter_type=ChapterType.CHAPTER,
        text="Configured voice generation test.",
    )

    session_factory = sessionmaker(
        bind=test_db.get_bind(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    queue = GenerationQueue(max_workers=0)
    await queue.start(session_factory, SlowGenerator())

    try:
        job_id = await queue.enqueue_book(book.id, test_db)
    finally:
        await queue.stop()

    job = test_db.query(GenerationJob).filter(GenerationJob.id == job_id).one()
    assert job.status == GenerationJobStatus.QUEUED
    assert job.voice_name == "Nova"
    assert job.emotion == "calm"
    assert job.speed == 1.15
