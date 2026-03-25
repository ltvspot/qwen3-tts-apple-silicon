"""Tests for catalog batch orchestration."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.orm import Session, sessionmaker

from src.database import (
    BatchRun,
    Book,
    BookExportStatus,
    GenerationJob,
    GenerationJobStatus,
    GenerationJobType,
    utc_now,
)
from src.pipeline.batch_orchestrator import BatchOrchestrator


class StubModelManager:
    """Minimal model manager state for orchestration tests."""

    class Stats:
        reload_count = 0

    stats = Stats()


class StubResourceMonitor:
    """Resource monitor stub with programmable responses."""

    def __init__(self, responses: list[tuple[bool, list[str]]] | None = None) -> None:
        self.responses = responses or [(True, [])]

    def check_can_proceed(self) -> tuple[bool, list[str]]:
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]


class StubQueue:
    """Queue stub that persists generation jobs and marks them complete."""

    def __init__(self, session_factory: sessionmaker[Session], *, delay_seconds: float = 0.01) -> None:
        self.session_factory = session_factory
        self.delay_seconds = delay_seconds
        self.enqueued: list[int] = []
        self.cancelled: list[int] = []

    async def enqueue_book(self, book_id: int, db_session: Session, *, priority: int, job_type: GenerationJobType):
        job = GenerationJob(
            book_id=book_id,
            job_type=job_type,
            status=GenerationJobStatus.QUEUED,
            priority=priority,
            chapters_total=1,
            chapters_completed=0,
            chapters_failed=0,
            force=False,
        )
        db_session.add(job)
        db_session.commit()
        db_session.refresh(job)
        self.enqueued.append(book_id)
        asyncio.create_task(self._complete_job(job.id))
        return job.id

    async def _complete_job(self, job_id: int) -> None:
        await asyncio.sleep(self.delay_seconds)
        with self.session_factory() as db_session:
            job = db_session.query(GenerationJob).filter(GenerationJob.id == job_id).one()
            started_at = utc_now()
            job.status = GenerationJobStatus.COMPLETED
            job.started_at = started_at
            job.completed_at = utc_now()
            job.chapters_completed = job.chapters_total
            db_session.commit()

    async def cancel_job(self, job_id: int, db_session: Session, *, reason: str) -> bool:
        job = db_session.query(GenerationJob).filter(GenerationJob.id == job_id).one()
        job.status = GenerationJobStatus.CANCELLED
        job.error_message = reason
        db_session.commit()
        self.cancelled.append(job_id)
        return True


def _create_book(test_db: Session, *, title: str, export_status: BookExportStatus = BookExportStatus.IDLE) -> Book:
    book = Book(
        title=title,
        author="Batch Author",
        folder_path=title.lower().replace(" ", "-"),
        export_status=export_status,
    )
    test_db.add(book)
    test_db.commit()
    test_db.refresh(book)
    return book


@pytest.mark.asyncio
async def test_batch_start_and_completion(test_db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """Starting a batch should persist progress and complete queued books."""

    real_sleep = asyncio.sleep

    async def fast_sleep(seconds: float) -> None:
        await real_sleep(0.01 if seconds else 0)

    monkeypatch.setattr("src.pipeline.batch_orchestrator.asyncio.sleep", fast_sleep)

    session_factory = sessionmaker(
        bind=test_db.get_bind(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    first_book = _create_book(test_db, title="Batch Book One")
    second_book = _create_book(test_db, title="Batch Book Two")

    orchestrator = BatchOrchestrator(
        StubQueue(session_factory),
        StubModelManager(),
        StubResourceMonitor(),
        session_factory,
    )
    orchestrator.resource_poll_interval_seconds = 0.01

    progress = await orchestrator.start_batch([first_book.id, second_book.id], priority="urgent")
    assert progress.total_books == 2

    await orchestrator.wait()
    persisted_run = test_db.query(BatchRun).filter(BatchRun.batch_id == progress.batch_id).one()

    assert orchestrator.to_dict()["status"] == "completed"
    assert persisted_run.books_completed == 2
    assert len(orchestrator.history()) == 1


@pytest.mark.asyncio
async def test_batch_skips_exported_books_and_respects_resource_pause(
    test_db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exported books should be skipped and low resources should pause the batch temporarily."""

    real_sleep = asyncio.sleep

    async def fast_sleep(seconds: float) -> None:
        await real_sleep(0.01 if seconds else 0)

    monkeypatch.setattr("src.pipeline.batch_orchestrator.asyncio.sleep", fast_sleep)

    session_factory = sessionmaker(
        bind=test_db.get_bind(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    skipped_book = _create_book(test_db, title="Already Exported", export_status=BookExportStatus.COMPLETED)
    queued_book = _create_book(test_db, title="Queued After Pause")
    resource_monitor = StubResourceMonitor(
        responses=[
            (False, ["LOW DISK: 5.0 GB free (minimum: 10.0 GB)"]),
            (True, []),
        ],
    )

    orchestrator = BatchOrchestrator(
        StubQueue(session_factory),
        StubModelManager(),
        resource_monitor,
        session_factory,
    )
    orchestrator.resource_poll_interval_seconds = 0.01

    await orchestrator.start_batch([skipped_book.id, queued_book.id], skip_already_exported=True)
    await orchestrator.wait()

    payload = orchestrator.to_dict()
    assert payload["books_skipped"] == 1
    assert payload["books_completed"] == 1
