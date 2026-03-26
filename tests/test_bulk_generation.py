"""Tests for bulk-generation hardening and export resilience."""

from __future__ import annotations

import asyncio
import subprocess
import tracemalloc
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session, sessionmaker

from src.config import FailureThresholdSettings, settings
from src.database import (
    Book,
    BookStatus,
    Chapter,
    ChapterStatus,
    ChapterType,
    GenerationJob,
    GenerationJobStatus,
    GenerationJobType,
    QAStatus,
    utc_now,
)
from src.pipeline.batch_orchestrator import BatchOrchestrator, BatchSchedulingStrategy
from src.pipeline.exporter import concatenate_chapters_sync
from src.pipeline.queue_manager import FailureTrackingStats, GenerationQueue
from src.utils.subprocess_utils import run_ffmpeg


def _create_book(test_db: Session, *, title: str, status: BookStatus = BookStatus.PARSED) -> Book:
    book = Book(
        title=title,
        author="Batch Author",
        folder_path=title.lower().replace(" ", "-"),
        status=status,
    )
    test_db.add(book)
    test_db.commit()
    test_db.refresh(book)
    return book


def _create_chapter(
    test_db: Session,
    *,
    book: Book,
    number: int,
    word_count: int = 100,
    title: str | None = None,
    qa_status: QAStatus = QAStatus.APPROVED,
    audio_path: str | None = None,
) -> Chapter:
    chapter = Chapter(
        book_id=book.id,
        number=number,
        title=title or f"Chapter {number}",
        type=ChapterType.CHAPTER,
        text_content="Narration text. " * 20,
        word_count=word_count,
        status=ChapterStatus.GENERATED if audio_path else ChapterStatus.PENDING,
        qa_status=qa_status,
        audio_path=audio_path,
        duration_seconds=1.0 if audio_path else None,
        audio_file_size_bytes=1 if audio_path else None,
    )
    test_db.add(chapter)
    test_db.commit()
    test_db.refresh(chapter)
    return chapter


class StubModelManager:
    class Stats:
        reload_count = 0

    stats = Stats()


class StubResourceMonitor:
    def check_can_proceed(self) -> tuple[bool, list[str]]:
        return (True, [])


class StubQueue:
    """Queue stub that marks jobs complete or failed without aborting the batch."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        failing_book_ids: set[int] | None = None,
        delay_seconds: float = 0.01,
    ) -> None:
        self.session_factory = session_factory
        self.failing_book_ids = failing_book_ids or set()
        self.delay_seconds = delay_seconds
        self.enqueued: list[int] = []

    async def enqueue_book(self, book_id: int, db_session: Session, *, priority: int, job_type: GenerationJobType):
        chapters_total = db_session.query(Chapter).filter(Chapter.book_id == book_id).count()
        job = GenerationJob(
            book_id=book_id,
            job_type=job_type,
            status=GenerationJobStatus.QUEUED,
            priority=priority,
            chapters_total=chapters_total,
            chapters_completed=0,
            chapters_failed=0,
            force=False,
        )
        db_session.add(job)
        db_session.commit()
        db_session.refresh(job)
        self.enqueued.append(book_id)
        asyncio.create_task(self._finish_job(job.id, should_fail=book_id in self.failing_book_ids))
        return job.id

    async def _finish_job(self, job_id: int, *, should_fail: bool) -> None:
        await asyncio.sleep(self.delay_seconds)
        with self.session_factory() as db_session:
            job = db_session.query(GenerationJob).filter(GenerationJob.id == job_id).one()
            started_at = utc_now()
            job.started_at = started_at
            job.completed_at = utc_now()
            if should_fail:
                job.status = GenerationJobStatus.FAILED
                job.chapters_failed = max(job.chapters_total, 1)
                job.error_message = "Book generation failed: corrupt manuscript"
            else:
                job.status = GenerationJobStatus.COMPLETED
                job.chapters_completed = job.chapters_total
            db_session.commit()

    async def cancel_job(self, job_id: int, db_session: Session, *, reason: str) -> bool:
        job = db_session.query(GenerationJob).filter(GenerationJob.id == job_id).one()
        job.status = GenerationJobStatus.CANCELLED
        job.error_message = reason
        db_session.commit()
        return True


class FakeAudioSegment:
    """Fake large audio payload used to verify streaming concatenation."""

    def __init__(self, raw_size_bytes: int = 11 * 1024 * 1024, duration_ms: int = 1000) -> None:
        self.raw_data = b"\x00" * raw_size_bytes
        self.frame_rate = 44100
        self.channels = 1
        self.sample_width = 2
        self._duration_ms = duration_ms

    def set_frame_rate(self, frame_rate: int) -> "FakeAudioSegment":
        self.frame_rate = frame_rate
        return self

    def set_channels(self, channels: int) -> "FakeAudioSegment":
        self.channels = channels
        return self

    def set_sample_width(self, sample_width: int) -> "FakeAudioSegment":
        self.sample_width = sample_width
        return self

    def __add__(self, _gain_db: float) -> "FakeAudioSegment":
        return self

    def __len__(self) -> int:
        return self._duration_ms


class DummyWaveWriter:
    """In-memory sink that discards streamed frames while mimicking wave.open()."""

    def __enter__(self) -> "DummyWaveWriter":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def setnchannels(self, _channels: int) -> None:
        return None

    def setsampwidth(self, _sample_width: int) -> None:
        return None

    def setframerate(self, _frame_rate: int) -> None:
        return None

    def writeframes(self, _frames: bytes) -> None:
        return None


def _write_placeholder_wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(44100)
        handle.writeframes(b"\x00\x00" * 4)


def test_percentage_threshold_stops_batch() -> None:
    queue = GenerationQueue(
        max_workers=1,
        failure_thresholds=FailureThresholdSettings(
            max_failure_rate_percent=5.0,
            max_consecutive_failures=10,
            min_chunks_for_rate=50,
        ),
    )

    should_stop, message = queue._should_stop_batch(
        FailureTrackingStats(total_chunks_processed=100, failed_chunks=6, consecutive_failures=2)
    )

    assert should_stop is True
    assert message == "Stopped: 6.0% failure rate (6/100)"


def test_consecutive_threshold_stops_batch() -> None:
    queue = GenerationQueue(
        max_workers=1,
        failure_thresholds=FailureThresholdSettings(
            max_failure_rate_percent=25.0,
            max_consecutive_failures=10,
            min_chunks_for_rate=50,
        ),
    )

    should_stop, message = queue._should_stop_batch(
        FailureTrackingStats(total_chunks_processed=20, failed_chunks=4, consecutive_failures=10)
    )

    assert should_stop is True
    assert message == "Stopped: 10 consecutive failures"


def test_rate_not_checked_below_minimum() -> None:
    queue = GenerationQueue(
        max_workers=1,
        failure_thresholds=FailureThresholdSettings(
            max_failure_rate_percent=5.0,
            max_consecutive_failures=10,
            min_chunks_for_rate=50,
        ),
    )

    should_stop, message = queue._should_stop_batch(
        FailureTrackingStats(total_chunks_processed=49, failed_chunks=10, consecutive_failures=1)
    )

    assert should_stop is False
    assert message == ""


@pytest.mark.asyncio
async def test_book_failure_isolated(test_db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
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
    first = _create_book(test_db, title="Stable One")
    second = _create_book(test_db, title="Broken Two")
    third = _create_book(test_db, title="Stable Three")
    for book in (first, second, third):
        _create_chapter(test_db, book=book, number=1)

    orchestrator = BatchOrchestrator(
        StubQueue(session_factory, failing_book_ids={second.id}),
        StubModelManager(),
        StubResourceMonitor(),
        session_factory,
    )
    orchestrator.resource_poll_interval_seconds = 0.01

    await orchestrator.start_batch([first.id, second.id, third.id])
    await orchestrator.wait()

    payload = orchestrator.to_dict()
    assert payload is not None
    assert payload["status"] == "completed"
    assert payload["books_completed"] == 2
    assert payload["books_failed"] == 1
    assert payload["summary"] == "Completed: 2 | Failed: 1 | Skipped: 0 | Remaining: 0"


def test_batch_estimate_disk(client, test_db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    first = _create_book(test_db, title="Estimate One")
    second = _create_book(test_db, title="Estimate Two")
    _create_chapter(test_db, book=first, number=1, word_count=120_000)
    _create_chapter(test_db, book=second, number=1, word_count=180_000)

    monkeypatch.setattr(
        "src.api.batch_routes.shutil.disk_usage",
        lambda _path: SimpleNamespace(free=20 * (1024**3), used=10 * (1024**3), total=30 * (1024**3)),
    )

    response = client.post(
        "/api/batch/estimate",
        json={"book_ids": [first.id, second.id], "skip_already_exported": True},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["books"] == 2
    assert payload["total_chapters"] == 2
    assert payload["total_words"] == 300000
    assert payload["estimated_audio_hours"] == pytest.approx(33.3, rel=0.01)
    assert payload["estimated_disk_gb"] == pytest.approx(12.0, rel=0.05)
    assert payload["can_proceed"] is True


def test_batch_estimate_warns_low_disk(client, test_db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    book = _create_book(test_db, title="Low Disk Book")
    _create_chapter(test_db, book=book, number=1, word_count=300_000)

    monkeypatch.setattr(
        "src.api.batch_routes.shutil.disk_usage",
        lambda _path: SimpleNamespace(free=1 * (1024**3), used=9 * (1024**3), total=10 * (1024**3)),
    )

    response = client.post(
        "/api/batch/estimate",
        json={"book_ids": [book.id], "skip_already_exported": True},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["can_proceed"] is False
    assert any("Low disk headroom" in warning for warning in payload["warnings"])


def test_batch_start_rejects_when_disk_headroom_is_insufficient(
    client,
    test_db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    book = _create_book(test_db, title="Tight Disk Book")
    _create_chapter(test_db, book=book, number=1, word_count=300_000)

    monkeypatch.setattr(
        "src.api.batch_routes.shutil.disk_usage",
        lambda _path: SimpleNamespace(free=5 * (1024**3), used=95 * (1024**3), total=100 * (1024**3)),
    )
    notified: list[str] = []
    monkeypatch.setattr(
        "src.api.batch_routes.send_batch_error_notification",
        lambda message: notified.append(message),
    )

    response = client.post(
        "/api/batch/start",
        json={"book_ids": [book.id], "skip_already_exported": True},
    )

    assert response.status_code == 507
    assert response.json()["detail"] == (
        "Insufficient disk space for batch. Estimated 12.0GB needed, 5.0GB available."
    )
    assert notified == [
        "Insufficient disk space for batch. Estimated 12.0GB needed, 5.0GB available."
    ]


@pytest.mark.asyncio
async def test_shortest_first_scheduling(test_db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
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
    long_book = _create_book(test_db, title="Fifty Chapters")
    short_book = _create_book(test_db, title="Five Chapters")
    for chapter_number in range(1, 51):
        _create_chapter(test_db, book=long_book, number=chapter_number)
    for chapter_number in range(1, 6):
        _create_chapter(test_db, book=short_book, number=chapter_number)

    queue = StubQueue(session_factory)
    orchestrator = BatchOrchestrator(
        queue,
        StubModelManager(),
        StubResourceMonitor(),
        session_factory,
    )
    orchestrator.resource_poll_interval_seconds = 0.01

    await orchestrator.start_batch(
        [long_book.id, short_book.id],
        strategy=BatchSchedulingStrategy.SHORTEST_FIRST,
    )
    await orchestrator.wait()

    assert queue.enqueued[:2] == [short_book.id, long_book.id]


def test_streaming_export_low_memory(
    test_db: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "OUTPUTS_PATH", str(tmp_path / "outputs"))
    monkeypatch.setattr("src.pipeline.exporter.measure_integrated_lufs", lambda _path: None)
    monkeypatch.setattr("src.pipeline.exporter.AudioSegment.from_wav", lambda _path: FakeAudioSegment())

    book = _create_book(test_db, title="Streaming Export")
    slug = book.title.lower().replace(" ", "-")
    for chapter_number in range(1, 21):
        relative_path = f"{book.id}-{slug}/chapters/{chapter_number:02d}-chapter.wav"
        absolute_path = Path(settings.OUTPUTS_PATH) / relative_path
        _write_placeholder_wav(absolute_path)
        _create_chapter(
            test_db,
            book=book,
            number=chapter_number,
            audio_path=relative_path,
            qa_status=QAStatus.APPROVED,
        )

    monkeypatch.setattr("src.pipeline.exporter.wave.open", lambda *_args, **_kwargs: DummyWaveWriter())

    session_factory = sessionmaker(
        bind=test_db.get_bind(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    tracemalloc.start()
    try:
        concatenate_chapters_sync(
            book.id,
            include_only_approved=True,
            chapter_silence_seconds=0.1,
            opening_silence_seconds=0.1,
            closing_silence_seconds=0.1,
            session_factory=session_factory,
        )
        _, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert peak_bytes < 200 * 1024 * 1024


def test_ffmpeg_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd=["ffmpeg", "-i", "bad.wav"], timeout=120)

    monkeypatch.setattr("src.utils.subprocess_utils.subprocess.run", raise_timeout)

    with pytest.raises(RuntimeError, match="ffmpeg timed out"):
        run_ffmpeg(["ffmpeg", "-i", "bad.wav"])
