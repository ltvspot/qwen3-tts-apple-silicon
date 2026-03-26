"""Tests for the generation pipeline, job queue, and generation API."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import timedelta
from pathlib import Path

import pytest
from pydub import AudioSegment
from pydub.generators import Sine
from sqlalchemy.orm import Session, sessionmaker

from src.api import generation as generation_api
from src.config import FailureThresholdSettings, settings
from src.database import (
    Book,
    BookStatus,
    Chapter,
    ChapterQARecord,
    ChapterStatus,
    ChapterType,
    GenerationJob,
    GenerationJobStatus,
    utc_now,
)
from src.engines.qwen3_tts import Qwen3TTS
from src.engines.voice_cloner import VoiceCloner
from src.pipeline.generator import AudiobookGenerator, ChunkGenerationExhaustedError, GenerationCancelled
from src.pipeline.queue_manager import GenerationQueue, JobStatus


def create_book(
    test_db: Session,
    *,
    title: str = "Test Book",
    status: BookStatus = BookStatus.PARSED,
) -> Book:
    """Create and persist a test book."""

    book = Book(
        title=title,
        author="Test Author",
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
    chapter_type: ChapterType,
    text: str,
) -> Chapter:
    """Create and persist a test chapter."""

    chapter = Chapter(
        book_id=book_id,
        number=number,
        title=title,
        type=chapter_type,
        text_content=text,
        word_count=len(text.split()),
        status=ChapterStatus.PENDING,
    )
    test_db.add(chapter)
    test_db.commit()
    test_db.refresh(chapter)
    return chapter


class SlowGenerator:
    """A deliberately slow generator stub used to exercise queue cancellation."""

    async def generate_book(
        self,
        book_id,
        db_session,
        progress_callback=None,
        should_cancel=None,
        force=False,
    ):
        del force
        for chapter_number in range(1, 5):
            if should_cancel and should_cancel():
                raise GenerationCancelled("Generation cancelled.")

            await asyncio.sleep(0.02)
            if progress_callback is not None:
                await progress_callback(chapter_number, chapter_number * 25)

        book = db_session.query(Book).filter(Book.id == book_id).first()
        if book is not None:
            book.status = BookStatus.GENERATED
            db_session.commit()

        return {
            "status": "success",
            "total_chapters": 4,
            "generated_chapters": 4,
            "failed_chapters": [],
            "total_duration": 4.0,
            "errors": [],
        }

    async def generate_chapter(
        self,
        book_id,
        chapter,
        db_session,
        progress_callback=None,
        should_cancel=None,
        force=False,
    ):
        del book_id, db_session, force
        for index in range(4):
            if should_cancel and should_cancel():
                raise GenerationCancelled("Generation cancelled.")
            await asyncio.sleep(0.01)
            if progress_callback is not None:
                await progress_callback((index + 1) / 4)
        chapter.status = ChapterStatus.GENERATED
        return 1.0


class FlakyEngine:
    """Test TTS engine that fails transiently before succeeding."""

    def __init__(self) -> None:
        self.calls = 0
        self.loaded = False
        self.max_chunk_chars = 10_000

    def load(self) -> None:
        self.loaded = True

    def unload(self) -> None:
        self.loaded = False

    def generate(self, text: str, voice: str, emotion: str | None = None, speed: float = 1.0) -> AudioSegment:
        del text, voice, emotion, speed
        self.calls += 1
        if self.calls < 3:
            raise TimeoutError("temporary timeout")
        return Sine(220).to_audio_segment(duration=250, volume=-6.0)


class ValidationFailureEngine:
    """Test engine that produces one invalid chunk repeatedly."""

    def __init__(self) -> None:
        self.calls = 0
        self.loaded = False
        self.max_chunk_chars = 15

    def load(self) -> None:
        self.loaded = True

    def unload(self) -> None:
        self.loaded = False

    def generate(self, text: str, voice: str, emotion: str | None = None, speed: float = 1.0) -> AudioSegment:
        del voice, emotion, speed
        self.calls += 1
        if "Invalid chunk." in text:
            return AudioSegment.silent(duration=250)
        return Sine(220).to_audio_segment(duration=400, volume=-6.0)


class SplitRetryEngine:
    """Test engine that only succeeds after a failed chunk is split by sentence."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.loaded = False
        self.max_chunk_chars = 500

    def load(self) -> None:
        self.loaded = True

    def unload(self) -> None:
        self.loaded = False

    def generate(self, text: str, voice: str, emotion: str | None = None, speed: float = 1.0) -> AudioSegment:
        del voice, emotion, speed
        self.calls.append(text)
        if "Alpha sentence." in text and "Beta sentence." in text:
            return AudioSegment.silent(duration=250)
        return Sine(220).to_audio_segment(duration=700, volume=-6.0).set_frame_rate(22050)


class RecordingCheckpointEngine:
    """Engine stub that records which chunks were regenerated during resume."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.loaded = False
        self.max_chunk_chars = 20

    def load(self) -> None:
        self.loaded = True

    def unload(self) -> None:
        self.loaded = False

    def generate(self, text: str, voice: str, emotion: str | None = None, speed: float = 1.0) -> AudioSegment:
        del voice, emotion, speed
        self.calls.append(text)
        return Sine(220).to_audio_segment(duration=700, volume=-6.0).set_frame_rate(22050)


class ConsecutiveFailureGenerator:
    """Queue generator stub that marks chapters failed and keeps failing."""

    async def generate_chapter(
        self,
        book_id,
        chapter,
        db_session,
        progress_callback=None,
        should_cancel=None,
        force=False,
        voice_name=None,
        emotion=None,
        speed=None,
    ):
        del book_id, progress_callback, should_cancel, force, voice_name, emotion, speed
        chapter.status = ChapterStatus.FAILED
        chapter.completed_at = utc_now()
        chapter.error_message = f"Chapter {chapter.number} failed"
        db_session.commit()
        raise RuntimeError(chapter.error_message)


class SingleChapterFailureGenerator:
    """Queue generator stub that fails a targeted single-chapter job."""

    async def generate_chapter(
        self,
        book_id,
        chapter,
        db_session,
        progress_callback=None,
        should_cancel=None,
        force=False,
        voice_name=None,
        emotion=None,
        speed=None,
    ):
        del book_id, progress_callback, should_cancel, force, voice_name, emotion, speed
        chapter.status = ChapterStatus.FAILED
        chapter.completed_at = utc_now()
        chapter.error_message = f"Chapter {chapter.number} failed"
        db_session.commit()
        raise RuntimeError(chapter.error_message)


@pytest.fixture(autouse=True)
def generation_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Route generated audio into a test-only output directory and use synthetic TTS."""

    monkeypatch.setattr(settings, "OUTPUTS_PATH", str(tmp_path / "outputs"))
    monkeypatch.setattr(settings, "TTS_BACKEND", "synthetic")


@pytest.mark.asyncio
async def test_generate_chapter_writes_audio_and_metadata(test_db: Session) -> None:
    """Single chapter generation should create a WAV and update DB metadata."""

    engine = Qwen3TTS(backend="synthetic")
    generator = AudiobookGenerator(engine)
    book = create_book(test_db, title="Signal Fires")
    chapter = create_chapter(
        test_db,
        book_id=book.id,
        number=1,
        title="First Light",
        chapter_type=ChapterType.CHAPTER,
        text="This is a test of the audiobook narrator. Hello world.",
    )

    duration = await generator.generate_chapter(book.id, chapter, test_db)

    test_db.refresh(chapter)
    assert duration > 0
    assert chapter.status == ChapterStatus.GENERATED
    assert chapter.duration_seconds == duration
    assert chapter.audio_path == "1-signal-fires/chapters/01-ch01-first-light.wav"
    assert chapter.current_chunk == chapter.total_chunks
    assert chapter.total_chunks is not None
    assert chapter.chunk_boundaries is not None

    audio_file = Path(settings.OUTPUTS_PATH) / chapter.audio_path
    assert audio_file.exists()
    assert audio_file.stat().st_size > 0
    assert audio_file.with_suffix(".qa.json").exists()
    boundaries = json.loads(chapter.chunk_boundaries)
    assert isinstance(boundaries, list)
    assert boundaries[0] == 0.0

    qa_record = (
        test_db.query(ChapterQARecord)
        .filter(ChapterQARecord.book_id == book.id, ChapterQARecord.chapter_n == chapter.number)
        .first()
    )
    assert qa_record is not None


@pytest.mark.asyncio
async def test_generate_book_processes_all_chapters_and_uses_credit_speed(test_db: Session) -> None:
    """Book generation should process chapters sequentially and slow down credits."""

    engine = Qwen3TTS(backend="synthetic")
    generator = AudiobookGenerator(engine)
    book = create_book(test_db, title="The Golden Thread")

    opening = create_chapter(
        test_db,
        book_id=book.id,
        number=0,
        title="Opening Credits",
        chapter_type=ChapterType.OPENING_CREDITS,
        text="This is The Golden Thread. Written by Test Author. Narrated by Kent Zimering.",
    )
    first_chapter = create_chapter(
        test_db,
        book_id=book.id,
        number=1,
        title="The Beginning",
        chapter_type=ChapterType.CHAPTER,
        text="The first chapter begins with a deliberate pace for testing.",
    )
    closing = create_chapter(
        test_db,
        book_id=book.id,
        number=2,
        title="Closing Credits",
        chapter_type=ChapterType.CLOSING_CREDITS,
        text="This was The Golden Thread. Written by Test Author. Narrated by Kent Zimering.",
    )

    original_generate = engine.generate
    requested_speeds: list[float] = []

    def record_generate(text: str, voice: str, emotion: str | None = None, speed: float = 1.0):
        requested_speeds.append(speed)
        return original_generate(text, voice, emotion, speed)

    engine.generate = record_generate  # type: ignore[method-assign]

    result = await generator.generate_book(book.id, test_db)

    test_db.refresh(book)
    test_db.refresh(opening)
    test_db.refresh(first_chapter)
    test_db.refresh(closing)

    assert result["status"] == "success"
    assert result["generated_chapters"] == 3
    assert book.status == BookStatus.GENERATED
    assert opening.audio_path == "1-the-golden-thread/chapters/00-opening-credits.wav"
    assert first_chapter.audio_path == "1-the-golden-thread/chapters/01-ch01-the-beginning.wav"
    assert closing.audio_path == "1-the-golden-thread/chapters/02-closing-credits.wav"
    assert requested_speeds == [0.9, 1.0, 0.9]


@pytest.mark.asyncio
async def test_generate_book_accepts_persisted_cloned_voice(test_db: Session, tmp_path: Path) -> None:
    """Book generation should accept a persisted cloned voice ID."""

    reference_path = tmp_path / "kent.wav"
    Sine(215).to_audio_segment(duration=2500).set_channels(1).export(reference_path, format="wav")
    VoiceCloner(settings.VOICES_PATH).clone_voice(
        "kent-zimering",
        reference_path,
        "This is the cloned reference transcript.",
    )

    engine = Qwen3TTS(backend="synthetic")
    generator = AudiobookGenerator(engine)
    book = create_book(test_db, title="Cloned Voice Book")
    create_chapter(
        test_db,
        book_id=book.id,
        number=1,
        title="Using a Clone",
        chapter_type=ChapterType.CHAPTER,
        text="This chapter should generate with the cloned voice name.",
    )

    result = await generator.generate_book(book.id, test_db, voice_name="kent-zimering")

    assert result["status"] == "success"
    generated_chapter = test_db.query(Chapter).filter(Chapter.book_id == book.id).one()
    assert generated_chapter.status == ChapterStatus.GENERATED
    assert generated_chapter.audio_path == "1-cloned-voice-book/chapters/01-ch01-using-a-clone.wav"


@pytest.mark.asyncio
async def test_generate_chapter_retries_transient_failures_before_succeeding(test_db: Session) -> None:
    """Transient engine failures should be retried before surfacing an error."""

    generator = AudiobookGenerator(FlakyEngine())
    book = create_book(test_db, title="Retry Book")
    chapter = create_chapter(
        test_db,
        book_id=book.id,
        number=1,
        title="Retry Chapter",
        chapter_type=ChapterType.CHAPTER,
        text="A short chapter that should succeed after transient failures.",
    )

    duration = await generator.generate_chapter(book.id, chapter, test_db)

    test_db.refresh(chapter)
    assert duration == 0.25
    assert generator.engine.calls == 3
    assert chapter.status == ChapterStatus.GENERATED


@pytest.mark.asyncio
async def test_generate_chapter_fails_when_chunk_exhaustion_is_unrecoverable(test_db: Session) -> None:
    """Repeated hard validation failures should fail the chapter instead of skipping audio."""

    generator = AudiobookGenerator(ValidationFailureEngine())
    book = create_book(test_db, title="Manual Review Book")
    chapter = create_chapter(
        test_db,
        book_id=book.id,
        number=1,
        title="Mixed Quality Chapter",
        chapter_type=ChapterType.CHAPTER,
        text="Valid chunk. Invalid chunk.",
    )

    with pytest.raises(ChunkGenerationExhaustedError, match="Chapter cannot be completed with missing audio"):
        await generator.generate_chapter(book.id, chapter, test_db)

    test_db.refresh(chapter)
    assert chapter.status == ChapterStatus.FAILED
    assert chapter.audio_path is None
    assert chapter.error_message is not None
    assert "Chunk 2 failed after 3 attempts" in chapter.error_message
    assert "Chapter cannot be completed with missing audio" in chapter.error_message
    assert generator.engine.calls == 4


@pytest.mark.asyncio
async def test_generate_chapter_retries_by_splitting_failed_chunk(test_db: Session) -> None:
    """A failed chunk should be retried as sentence-level sub-chunks before aborting the chapter."""

    engine = SplitRetryEngine()
    generator = AudiobookGenerator(engine)
    book = create_book(test_db, title="Split Retry Book")
    chapter = create_chapter(
        test_db,
        book_id=book.id,
        number=1,
        title="Split Retry Chapter",
        chapter_type=ChapterType.CHAPTER,
        text="Alpha sentence. Beta sentence.",
    )

    duration = await generator.generate_chapter(book.id, chapter, test_db)

    test_db.refresh(chapter)
    assert duration > 1.7
    assert chapter.status == ChapterStatus.GENERATED
    assert len([call for call in engine.calls if "Alpha sentence." in call and "Beta sentence." in call]) == 3
    assert any(call == "Alpha sentence." for call in engine.calls)
    assert any(call == "Beta sentence." for call in engine.calls)


@pytest.mark.asyncio
async def test_generate_chapter_resumes_from_chunk_checkpoints(test_db: Session) -> None:
    """Existing chunk checkpoints should be reused when resuming a failed chapter."""

    engine = RecordingCheckpointEngine()
    generator = AudiobookGenerator(engine)
    book = create_book(test_db, title="Checkpoint Resume Book")
    chapter = create_chapter(
        test_db,
        book_id=book.id,
        number=1,
        title="Checkpoint Resume Chapter",
        chapter_type=ChapterType.CHAPTER,
        text="First chunk. Second chunk.",
    )

    checkpoint_audio = Sine(220).to_audio_segment(duration=700, volume=-6.0).set_frame_rate(22050)
    checkpoint_path = generator._get_chunk_checkpoint_path(book.id, chapter, 0)
    generator._save_chunk_checkpoint(checkpoint_path, checkpoint_audio)

    duration = await generator.generate_chapter(book.id, chapter, test_db)

    test_db.refresh(chapter)
    metadata = json.loads(chapter.generation_metadata or "{}")
    assert duration > 0
    assert chapter.status == ChapterStatus.GENERATED
    assert metadata["checkpoints"]["checkpoint_hits"] == 1
    assert metadata["checkpoints"]["chunk_files_written"] >= 1
    assert engine.calls == ["Second chunk."]


@pytest.mark.asyncio
async def test_generation_queue_processes_fifo_and_cancels_queued_jobs(test_db: Session) -> None:
    """The queue should process jobs in order and allow queued cancellation."""

    queue = GenerationQueue(max_workers=1)
    session_factory = sessionmaker(
        bind=test_db.get_bind(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    first_book = create_book(test_db, title="Queue Book One")
    second_book = create_book(test_db, title="Queue Book Two")

    await queue.start(session_factory, SlowGenerator())

    first_job_id = await queue.enqueue_book(first_book.id, test_db)
    second_job_id = await queue.enqueue_book(second_book.id, test_db)

    await asyncio.sleep(0.01)
    cancelled = await queue.cancel_job(second_job_id, test_db)
    assert cancelled is True

    await queue.wait_until_idle()

    first_status = await queue.get_job_status(first_job_id, test_db)
    second_status = await queue.get_job_status(second_job_id, test_db)

    assert first_status is not None
    assert second_status is not None
    assert first_status.status == JobStatus.COMPLETED
    assert second_status.status == JobStatus.CANCELLED

    await queue.stop()


@pytest.mark.asyncio
async def test_single_chapter_generation_does_not_promote_book(test_db: Session) -> None:
    """Completing one chapter should not mark the full book as generated."""

    queue = GenerationQueue(max_workers=1)
    session_factory = sessionmaker(
        bind=test_db.get_bind(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    book = create_book(test_db, title="Partial Queue Book")
    for chapter_number in range(1, 6):
        create_chapter(
            test_db,
            book_id=book.id,
            number=chapter_number,
            title=f"Chapter {chapter_number}",
            chapter_type=ChapterType.CHAPTER,
            text="A queue test chapter.",
        )

    await queue.start(session_factory, SlowGenerator())

    job_id = await queue.enqueue_chapter(book.id, 1, test_db)
    await queue.wait_until_idle()

    test_db.expire_all()
    job_status = await queue.get_job_status(job_id, test_db)
    persisted_book = test_db.query(Book).filter(Book.id == book.id).one()
    chapters = test_db.query(Chapter).filter(Chapter.book_id == book.id).order_by(Chapter.number).all()

    assert job_status is not None
    assert job_status.status == JobStatus.COMPLETED
    assert persisted_book.status == BookStatus.PARSED
    assert persisted_book.generation_status == "idle"
    assert [chapter.status for chapter in chapters] == [
        ChapterStatus.GENERATED,
        ChapterStatus.PENDING,
        ChapterStatus.PENDING,
        ChapterStatus.PENDING,
        ChapterStatus.PENDING,
    ]

    await queue.stop()


@pytest.mark.asyncio
async def test_all_chapters_generated_promotes_book(test_db: Session) -> None:
    """Completing every chapter through the queue should mark the book generated."""

    queue = GenerationQueue(max_workers=1)
    session_factory = sessionmaker(
        bind=test_db.get_bind(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    book = create_book(test_db, title="Complete Queue Book")
    for chapter_number in range(1, 4):
        create_chapter(
            test_db,
            book_id=book.id,
            number=chapter_number,
            title=f"Chapter {chapter_number}",
            chapter_type=ChapterType.CHAPTER,
            text="A queue completion chapter.",
        )

    await queue.start(session_factory, SlowGenerator())

    job_id = await queue.enqueue_book(book.id, test_db)
    await queue.wait_until_idle()

    test_db.expire_all()
    job_status = await queue.get_job_status(job_id, test_db)
    persisted_book = test_db.query(Book).filter(Book.id == book.id).one()
    chapters = test_db.query(Chapter).filter(Chapter.book_id == book.id).all()

    assert job_status is not None
    assert job_status.status == JobStatus.COMPLETED
    assert persisted_book.status == BookStatus.GENERATED
    assert all(chapter.status == ChapterStatus.GENERATED for chapter in chapters)

    await queue.stop()


@pytest.mark.asyncio
async def test_failed_generation_does_not_promote_book(test_db: Session) -> None:
    """A failed single-chapter job should never promote the parent book to generated."""

    queue = GenerationQueue(max_workers=1)
    session_factory = sessionmaker(
        bind=test_db.get_bind(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    book = create_book(test_db, title="Failed Queue Book")
    for chapter_number in range(1, 3):
        create_chapter(
            test_db,
            book_id=book.id,
            number=chapter_number,
            title=f"Chapter {chapter_number}",
            chapter_type=ChapterType.CHAPTER,
            text="A failing queue chapter.",
        )

    await queue.start(session_factory, SingleChapterFailureGenerator())

    job_id = await queue.enqueue_chapter(book.id, 1, test_db)
    await queue.wait_until_idle()

    test_db.expire_all()
    job_status = await queue.get_job_status(job_id, test_db)
    persisted_book = test_db.query(Book).filter(Book.id == book.id).one()

    assert job_status is not None
    assert job_status.status == JobStatus.FAILED
    assert persisted_book.status == BookStatus.PARSED
    assert persisted_book.generation_status == "error"

    await queue.stop()


@pytest.mark.asyncio
async def test_generation_queue_stops_after_three_consecutive_chapter_failures(test_db: Session) -> None:
    """The queue should abort a full-book job after three consecutive chapter failures."""

    queue = GenerationQueue(
        max_workers=1,
        failure_thresholds=FailureThresholdSettings(
            max_failure_rate_percent=50.0,
            max_consecutive_failures=3,
            min_chunks_for_rate=500,
        ),
    )
    session_factory = sessionmaker(
        bind=test_db.get_bind(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    book = create_book(test_db, title="Failure Cascade Book")
    for chapter_number in range(1, 5):
        create_chapter(
            test_db,
            book_id=book.id,
            number=chapter_number,
            title=f"Chapter {chapter_number}",
            chapter_type=ChapterType.CHAPTER,
            text="A failing chapter body.",
        )

    await queue.start(session_factory, ConsecutiveFailureGenerator())

    job_id = await queue.enqueue_book(book.id, test_db)
    await queue.wait_until_idle()

    test_db.expire_all()
    job_status = await queue.get_job_status(job_id, test_db)
    db_job = test_db.query(GenerationJob).filter(GenerationJob.id == job_id).one()
    chapters = test_db.query(Chapter).filter(Chapter.book_id == book.id).order_by(Chapter.number).all()

    assert job_status is not None
    assert job_status.status == JobStatus.FAILED
    assert db_job.error_message == "Stopped: 3 consecutive failures"
    assert [chapter.status for chapter in chapters] == [
        ChapterStatus.FAILED,
        ChapterStatus.FAILED,
        ChapterStatus.FAILED,
        ChapterStatus.PENDING,
    ]

    await queue.stop()


def test_generation_api_queues_job_tracks_status_and_serves_audio(client, test_db: Session) -> None:
    """The generation API should queue jobs, expose status, and serve chapter audio."""

    book = create_book(test_db, title="API Generation Book")
    chapter = create_chapter(
        test_db,
        book_id=book.id,
        number=1,
        title="Chapter One",
        chapter_type=ChapterType.CHAPTER,
        text="This paragraph exists to verify end-to-end API generation.",
    )

    response = client.post(f"/api/book/{book.id}/generate", json={})
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "queued"
    assert payload["book_id"] == book.id

    job_id = payload["job_id"]
    terminal_status = None
    for _ in range(50):
        status_response = client.get(f"/api/job/{job_id}")
        assert status_response.status_code == 200
        terminal_status = status_response.json()
        if terminal_status["status"] in {"completed", "failed"}:
            break
        time.sleep(0.02)

    assert terminal_status is not None
    assert terminal_status["status"] == "completed"
    assert terminal_status["progress"] == 100.0

    test_db.refresh(chapter)
    audio_response = client.get(f"/api/book/{book.id}/chapter/{chapter.number}/audio")
    assert audio_response.status_code == 200
    assert audio_response.headers["content-type"] == "audio/wav"
    assert chapter.audio_path is not None


def test_book_status_endpoint_returns_idle_shape(client, test_db: Session) -> None:
    """Idle status should include all chapters in prompt-09 polling shape."""

    book = create_book(test_db, title="Idle Status Book")
    create_chapter(
        test_db,
        book_id=book.id,
        number=0,
        title="Opening Credits",
        chapter_type=ChapterType.OPENING_CREDITS,
        text="Opening credits text.",
    )
    create_chapter(
        test_db,
        book_id=book.id,
        number=1,
        title="Chapter One",
        chapter_type=ChapterType.CHAPTER,
        text="Body text for idle polling shape verification.",
    )

    response = client.get(f"/api/book/{book.id}/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["book_id"] == book.id
    assert payload["status"] == "idle"
    assert payload["current_chapter_n"] is None
    assert payload["eta_seconds"] is None
    assert [chapter["status"] for chapter in payload["chapters"]] == ["pending", "pending"]
    assert payload["chapters"][1]["expected_total_seconds"] == 2.8


def test_status_endpoints_return_generating_progress_and_eta(client, test_db: Session) -> None:
    """Status polling should expose per-chapter progress and ETA using recent completions."""

    book = create_book(test_db, title="Progress Status Book")
    now = utc_now()

    completed_one = create_chapter(
        test_db,
        book_id=book.id,
        number=0,
        title="Opening Credits",
        chapter_type=ChapterType.OPENING_CREDITS,
        text=("one two three " * 25).strip(),
    )
    completed_two = create_chapter(
        test_db,
        book_id=book.id,
        number=1,
        title="Chapter One",
        chapter_type=ChapterType.CHAPTER,
        text=("one two three " * 25).strip(),
    )
    generating = create_chapter(
        test_db,
        book_id=book.id,
        number=2,
        title="Chapter Two",
        chapter_type=ChapterType.CHAPTER,
        text=("one two three " * 25).strip(),
    )
    create_chapter(
        test_db,
        book_id=book.id,
        number=3,
        title="Chapter Three",
        chapter_type=ChapterType.CHAPTER,
        text=("one two three " * 25).strip(),
    )

    completed_one.status = ChapterStatus.GENERATED
    completed_one.started_at = now - timedelta(seconds=120)
    completed_one.completed_at = now - timedelta(seconds=90)
    completed_one.duration_seconds = 30.0

    completed_two.status = ChapterStatus.GENERATED
    completed_two.started_at = now - timedelta(seconds=80)
    completed_two.completed_at = now - timedelta(seconds=50)
    completed_two.duration_seconds = 30.0

    generating.status = ChapterStatus.GENERATING
    generating.started_at = now - timedelta(seconds=10)
    generating.current_chunk = 5
    generating.total_chunks = 12

    test_db.add(
        GenerationJob(
            book_id=book.id,
            chapter_id=generating.id,
            status=GenerationJobStatus.RUNNING,
            progress=50.0,
            started_at=now - timedelta(seconds=10),
            force=False,
        ),
    )
    test_db.commit()

    book_status_response = client.get(f"/api/book/{book.id}/status")
    chapter_status_response = client.get(f"/api/book/{book.id}/chapter/{generating.number}/status")

    assert book_status_response.status_code == 200
    book_payload = book_status_response.json()
    assert book_payload["status"] == "generating"
    assert book_payload["current_chapter_n"] == generating.number
    assert book_payload["current_chunk"] == 5
    assert book_payload["total_chunks"] == 12
    assert book_payload["eta_seconds"] == 45

    generating_payload = next(
        chapter for chapter in book_payload["chapters"] if chapter["chapter_n"] == generating.number
    )
    assert generating_payload["status"] == "generating"
    assert generating_payload["expected_total_seconds"] == 30.0
    assert generating_payload["progress_seconds"] == 15.0
    assert generating_payload["current_chunk"] == 5
    assert generating_payload["total_chunks"] == 12

    assert chapter_status_response.status_code == 200
    chapter_payload = chapter_status_response.json()
    assert chapter_payload["status"] == "generating"
    assert chapter_payload["progress_seconds"] == 15.0
    assert chapter_payload["expected_total_seconds"] == 30.0
    assert chapter_payload["current_chunk"] == 5
    assert chapter_payload["total_chunks"] == 12


def test_status_endpoints_surface_generation_errors(client, test_db: Session) -> None:
    """Failed chapter state should be surfaced through the new polling endpoints."""

    book = create_book(test_db, title="Error Status Book")
    failed_chapter = create_chapter(
        test_db,
        book_id=book.id,
        number=1,
        title="Broken Chapter",
        chapter_type=ChapterType.CHAPTER,
        text="This chapter fails in a controlled test.",
    )
    failed_chapter.status = ChapterStatus.FAILED
    failed_chapter.error_message = "Synthetic generation failure."
    test_db.commit()

    response = client.get(f"/api/book/{book.id}/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "error"
    assert payload["chapters"][0]["status"] == "error"
    assert payload["chapters"][0]["error_message"] == "Synthetic generation failure."
