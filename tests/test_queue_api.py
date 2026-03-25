"""Tests for the production queue API."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

from src.api import generation_runtime
from src.database import (
    Book,
    BookStatus,
    Chapter,
    ChapterStatus,
    ChapterType,
    GenerationJob,
    GenerationJobStatus,
    GenerationJobType,
    JobHistory,
    utc_now,
)
from src.pipeline.queue_manager import GenerationQueue


def create_book(test_db: Session, *, title: str, status: BookStatus = BookStatus.PARSED) -> Book:
    """Create and persist a test book."""

    book = Book(
        title=title,
        author="Queue Author",
        folder_path=title.lower().replace(" ", "-"),
        status=status,
    )
    test_db.add(book)
    test_db.commit()
    test_db.refresh(book)
    return book


def create_chapter(
    test_db: Session,
    *,
    book_id: int,
    number: int,
    title: str,
    status: ChapterStatus = ChapterStatus.PENDING,
    word_count: int = 75,
) -> Chapter:
    """Create and persist a chapter."""

    chapter = Chapter(
        book_id=book_id,
        number=number,
        title=title,
        type=ChapterType.CHAPTER,
        text_content="one two three " * max(word_count // 3, 1),
        word_count=word_count,
        status=status,
    )
    test_db.add(chapter)
    test_db.commit()
    test_db.refresh(chapter)
    return chapter


@pytest.fixture(autouse=True)
def isolated_queue_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep queue endpoint tests deterministic by disabling worker processing."""

    class StubGenerator:
        def close(self) -> None:
            pass

    monkeypatch.setattr(generation_runtime, "_queue", GenerationQueue(max_workers=0))
    monkeypatch.setattr(generation_runtime, "_generator", StubGenerator())


def test_get_queue_returns_ordered_jobs_and_stats(client, test_db: Session) -> None:
    """Queue listing should order running first, then priority, and exclude stale terminal jobs."""

    now = utc_now()

    running_book = create_book(test_db, title="Running Book")
    queued_book = create_book(test_db, title="Queued Book")
    paused_book = create_book(test_db, title="Paused Book")
    stale_book = create_book(test_db, title="Stale Book")

    create_chapter(test_db, book_id=running_book.id, number=1, title="Chapter One", status=ChapterStatus.GENERATING)
    create_chapter(test_db, book_id=running_book.id, number=2, title="Chapter Two")
    create_chapter(test_db, book_id=queued_book.id, number=1, title="Queued One")
    create_chapter(test_db, book_id=paused_book.id, number=1, title="Paused One")
    create_chapter(test_db, book_id=stale_book.id, number=1, title="Old One")

    running_job = GenerationJob(
        book_id=running_book.id,
        job_type=GenerationJobType.FULL_BOOK,
        status=GenerationJobStatus.RUNNING,
        progress=50.0,
        current_chapter_progress=25.0,
        chapters_total=2,
        chapters_completed=1,
        chapters_failed=0,
        current_chapter_n=1,
        priority=10,
        created_at=now - timedelta(minutes=10),
        started_at=now - timedelta(minutes=5),
        eta_seconds=30,
        avg_seconds_per_chapter=15.0,
        force=False,
    )
    queued_job = GenerationJob(
        book_id=queued_book.id,
        job_type=GenerationJobType.FULL_BOOK,
        status=GenerationJobStatus.QUEUED,
        progress=0.0,
        current_chapter_progress=0.0,
        chapters_total=1,
        chapters_completed=0,
        chapters_failed=0,
        current_chapter_n=1,
        priority=80,
        created_at=now - timedelta(minutes=9),
        eta_seconds=40,
        force=False,
    )
    paused_job = GenerationJob(
        book_id=paused_book.id,
        job_type=GenerationJobType.FULL_BOOK,
        status=GenerationJobStatus.PAUSED,
        progress=0.0,
        current_chapter_progress=0.0,
        chapters_total=1,
        chapters_completed=0,
        chapters_failed=0,
        current_chapter_n=1,
        priority=20,
        created_at=now - timedelta(minutes=8),
        paused_at=now - timedelta(minutes=1),
        eta_seconds=20,
        force=False,
    )
    stale_completed_job = GenerationJob(
        book_id=stale_book.id,
        job_type=GenerationJobType.FULL_BOOK,
        status=GenerationJobStatus.COMPLETED,
        progress=100.0,
        current_chapter_progress=0.0,
        chapters_total=1,
        chapters_completed=1,
        chapters_failed=0,
        priority=0,
        created_at=now - timedelta(days=9),
        completed_at=now - timedelta(days=8),
        force=False,
    )
    test_db.add_all([running_job, queued_job, paused_job, stale_completed_job])
    test_db.commit()

    response = client.get("/api/queue")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_count"] == 3
    assert payload["active_job_count"] == 3
    assert payload["queue_stats"]["total_books_in_queue"] == 3
    assert payload["queue_stats"]["total_chapters"] == 3
    assert payload["queue_stats"]["estimated_total_time_seconds"] == 90
    assert [job["status"] for job in payload["jobs"]] == ["generating", "queued", "paused"]
    assert [job["book_title"] for job in payload["jobs"]] == ["Running Book", "Queued Book", "Paused Book"]
    assert payload["jobs"][0]["current_chapter_title"] == "Chapter One"


def test_queue_list_with_completed_jobs_no_crash(client, test_db: Session) -> None:
    """Completed jobs with naive SQLite timestamps should not crash queue listing."""

    book = create_book(test_db, title="Naive Timestamp Book")
    create_chapter(test_db, book_id=book.id, number=1, title="Completed Chapter", status=ChapterStatus.GENERATED)

    test_db.add(
        GenerationJob(
            book_id=book.id,
            job_type=GenerationJobType.FULL_BOOK,
            status=GenerationJobStatus.COMPLETED,
            progress=100.0,
            current_chapter_progress=0.0,
            chapters_total=1,
            chapters_completed=1,
            chapters_failed=0,
            priority=0,
            created_at=utc_now().replace(tzinfo=None),
            completed_at=utc_now().replace(tzinfo=None),
            force=False,
        )
    )
    test_db.commit()

    response = client.get("/api/queue")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_count"] == 1
    assert payload["jobs"][0]["status"] == "completed"


def test_get_queue_job_returns_breakdown_and_history(client, test_db: Session) -> None:
    """Detailed job view should include chapter breakdown and history log."""

    now = utc_now()
    book = create_book(test_db, title="Detail Book")
    completed_chapter = create_chapter(
        test_db,
        book_id=book.id,
        number=1,
        title="Completed Chapter",
        status=ChapterStatus.GENERATED,
    )
    active_chapter = create_chapter(
        test_db,
        book_id=book.id,
        number=2,
        title="Active Chapter",
        status=ChapterStatus.GENERATING,
    )
    completed_chapter.started_at = now - timedelta(seconds=60)
    completed_chapter.completed_at = now - timedelta(seconds=30)
    completed_chapter.duration_seconds = 30.0
    active_chapter.started_at = now - timedelta(seconds=15)
    test_db.commit()

    job = GenerationJob(
        book_id=book.id,
        job_type=GenerationJobType.FULL_BOOK,
        status=GenerationJobStatus.RUNNING,
        progress=50.0,
        current_chapter_progress=50.0,
        chapters_total=2,
        chapters_completed=1,
        chapters_failed=0,
        current_chapter_n=2,
        priority=30,
        created_at=now - timedelta(minutes=5),
        started_at=now - timedelta(minutes=3),
        eta_seconds=15,
        avg_seconds_per_chapter=30.0,
        force=False,
    )
    test_db.add(job)
    test_db.commit()
    test_db.refresh(job)

    test_db.add(
        JobHistory(
            job_id=job.id,
            book_id=book.id,
            action="resumed",
            details="User resumed from pause.",
            timestamp=now - timedelta(minutes=1),
        )
    )
    test_db.commit()

    response = client.get(f"/api/queue/{job.id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["book_title"] == "Detail Book"
    assert payload["status"] == "generating"
    assert payload["chapter_breakdown"][0]["status"] == "completed"
    assert payload["chapter_breakdown"][1]["status"] == "generating"
    assert payload["chapter_breakdown"][1]["progress_seconds"] == 15.0
    assert payload["history"][0]["action"] == "resumed"
    assert payload["history"][0]["details"] == "User resumed from pause."


def test_pause_resume_and_cancel_endpoints_persist_history(client, test_db: Session) -> None:
    """Pause, resume, and cancel should update job state and write history rows."""

    book = create_book(test_db, title="Control Book")
    create_chapter(test_db, book_id=book.id, number=1, title="Control Chapter")
    job = GenerationJob(
        book_id=book.id,
        job_type=GenerationJobType.FULL_BOOK,
        status=GenerationJobStatus.QUEUED,
        progress=0.0,
        current_chapter_progress=0.0,
        chapters_total=1,
        chapters_completed=0,
        chapters_failed=0,
        current_chapter_n=1,
        priority=10,
        force=False,
    )
    test_db.add(job)
    test_db.commit()
    test_db.refresh(job)

    pause_response = client.post(f"/api/queue/{job.id}/pause", json={"reason": "Testing pause"})
    assert pause_response.status_code == 200
    assert pause_response.json()["status"] == "paused"

    test_db.refresh(job)
    assert job.status == GenerationJobStatus.PAUSED

    resume_response = client.post(f"/api/queue/{job.id}/resume")
    assert resume_response.status_code == 200
    assert resume_response.json()["status"] == "queued"

    test_db.refresh(job)
    assert job.status == GenerationJobStatus.QUEUED

    cancel_response = client.post(f"/api/queue/{job.id}/cancel", json={"reason": "Testing cancel"})
    assert cancel_response.status_code == 200
    assert cancel_response.json()["status"] == "error"

    test_db.refresh(job)
    assert job.status == GenerationJobStatus.CANCELLED
    assert job.error_message == "Testing cancel"

    history_actions = [
        action for (action,) in test_db.query(JobHistory.action).filter(JobHistory.job_id == job.id).all()
    ]
    assert history_actions == ["paused", "resumed", "cancelled"]


def test_resume_book_generation_queues_the_first_incomplete_chapter(client, test_db: Session) -> None:
    """Resuming a book should target the first chapter without generated audio."""

    book = create_book(test_db, title="Resume API Book")
    finished = create_chapter(
        test_db,
        book_id=book.id,
        number=1,
        title="Finished Chapter",
        status=ChapterStatus.GENERATED,
    )
    finished.audio_path = "resume-api-book/01-finished.wav"
    finished.duration_seconds = 12.5
    create_chapter(
        test_db,
        book_id=book.id,
        number=2,
        title="Needs Retry",
        status=ChapterStatus.FAILED,
    )
    create_chapter(
        test_db,
        book_id=book.id,
        number=3,
        title="Still Pending",
    )
    test_db.commit()

    response = client.post(f"/api/book/{book.id}/resume")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "queued"
    assert payload["book_id"] == book.id
    assert payload["chapter_number"] == 2
    assert payload["message"] == "Generation resumed from chapter 2"

    job = test_db.query(GenerationJob).filter(GenerationJob.id == payload["job_id"]).one()
    assert job.status == GenerationJobStatus.QUEUED
    assert job.current_chapter_n == 2


def test_pause_running_job_sets_pause_request(client, test_db: Session) -> None:
    """Pausing an active job should record a deferred pause request."""

    book = create_book(test_db, title="Running Control Book")
    create_chapter(test_db, book_id=book.id, number=1, title="Running Chapter", status=ChapterStatus.GENERATING)
    job = GenerationJob(
        book_id=book.id,
        job_type=GenerationJobType.FULL_BOOK,
        status=GenerationJobStatus.RUNNING,
        progress=10.0,
        current_chapter_progress=25.0,
        chapters_total=1,
        chapters_completed=0,
        chapters_failed=0,
        current_chapter_n=1,
        priority=15,
        force=False,
    )
    test_db.add(job)
    test_db.commit()
    test_db.refresh(job)

    response = client.post(f"/api/queue/{job.id}/pause", json={"reason": "Pause running job"})

    assert response.status_code == 200
    assert response.json()["status"] == "paused"
    test_db.refresh(job)
    assert job.status == GenerationJobStatus.RUNNING
    assert job.pause_requested is True
    assert job.paused_at is not None


def test_priority_endpoint_reorders_job(client, test_db: Session) -> None:
    """Priority changes should clamp and report the resulting queue position."""

    first_book = create_book(test_db, title="First Priority Book")
    second_book = create_book(test_db, title="Second Priority Book")
    create_chapter(test_db, book_id=first_book.id, number=1, title="First Chapter")
    create_chapter(test_db, book_id=second_book.id, number=1, title="Second Chapter")

    first_job = GenerationJob(
        book_id=first_book.id,
        job_type=GenerationJobType.FULL_BOOK,
        status=GenerationJobStatus.QUEUED,
        progress=0.0,
        current_chapter_progress=0.0,
        chapters_total=1,
        chapters_completed=0,
        chapters_failed=0,
        current_chapter_n=1,
        priority=10,
        force=False,
    )
    second_job = GenerationJob(
        book_id=second_book.id,
        job_type=GenerationJobType.FULL_BOOK,
        status=GenerationJobStatus.QUEUED,
        progress=0.0,
        current_chapter_progress=0.0,
        chapters_total=1,
        chapters_completed=0,
        chapters_failed=0,
        current_chapter_n=1,
        priority=30,
        force=False,
    )
    test_db.add_all([first_job, second_job])
    test_db.commit()
    test_db.refresh(first_job)

    response = client.put(f"/api/queue/{first_job.id}/priority", json={"priority": 90})

    assert response.status_code == 200
    payload = response.json()
    assert payload["priority"] == 90
    assert payload["queue_position"] == 1

    test_db.refresh(first_job)
    assert first_job.priority == 90


def test_batch_all_creates_jobs_for_parsed_books(client, test_db: Session) -> None:
    """Batch queueing should create one queued job per parsed book without an active job."""

    first_book = create_book(test_db, title="Batch One")
    second_book = create_book(test_db, title="Batch Two")
    skipped_book = create_book(test_db, title="Skipped Draft", status=BookStatus.NOT_STARTED)
    active_book = create_book(test_db, title="Already Queued")

    create_chapter(test_db, book_id=first_book.id, number=1, title="Chapter One")
    create_chapter(test_db, book_id=second_book.id, number=1, title="Chapter Two")
    create_chapter(test_db, book_id=skipped_book.id, number=1, title="Draft Chapter")
    create_chapter(test_db, book_id=active_book.id, number=1, title="Queued Chapter")

    test_db.add(
        GenerationJob(
            book_id=active_book.id,
            job_type=GenerationJobType.FULL_BOOK,
            status=GenerationJobStatus.QUEUED,
            progress=0.0,
            current_chapter_progress=0.0,
            chapters_total=1,
            chapters_completed=0,
            chapters_failed=0,
            current_chapter_n=1,
            priority=0,
            force=False,
        )
    )
    test_db.commit()

    response = client.post(
        "/api/queue/batch-all",
        json={"priority": 25, "voice": "Ethan", "emotion": "neutral", "speed": 1.1},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["jobs_created"] == 2
    assert payload["books_queued"] == 2
    assert payload["total_chapters"] == 2
    assert payload["estimated_completion_seconds"] == 60

    created_jobs = (
        test_db.query(GenerationJob)
        .filter(GenerationJob.book_id.in_([first_book.id, second_book.id]))
        .order_by(GenerationJob.book_id.asc())
        .all()
    )
    assert len(created_jobs) == 2
    assert all(job.status == GenerationJobStatus.QUEUED for job in created_jobs)
    assert all(job.priority == 25 for job in created_jobs)


def test_missing_job_returns_not_found(client) -> None:
    """Missing queue jobs should return 404."""

    response = client.get("/api/queue/999")

    assert response.status_code == 404
