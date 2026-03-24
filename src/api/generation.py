"""Generation endpoints for queueing and monitoring audiobook synthesis jobs."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session, sessionmaker

from src.config import settings
from src.database import Book, BookStatus, Chapter, GenerationJob, get_db
from src.engines.qwen3_tts import Qwen3TTS
from src.pipeline.generator import AudiobookGenerator
from src.pipeline.queue_manager import GenerationQueue, JobInfo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["generation"])

_generator: AudiobookGenerator | None = None
_queue: GenerationQueue | None = None


class GenerationRequest(BaseModel):
    """Request payload for generation operations."""

    model_config = ConfigDict(extra="forbid")


class QueueJobResponse(BaseModel):
    """Response returned when a generation job is queued."""

    job_id: int
    status: str
    book_id: int
    chapter_number: int | None = None
    message: str


class JobStatusResponse(BaseModel):
    """Serialized status for a generation job."""

    job_id: int
    book_id: int
    chapter_id: int | None
    status: str
    progress: float
    error_message: str | None = None


class CancelJobResponse(BaseModel):
    """Response returned when cancelling a job."""

    cancelled: bool
    job_id: int
    message: str


def get_generator() -> AudiobookGenerator:
    """Return the lazily constructed audiobook generator singleton."""

    global _generator
    if _generator is None:
        _generator = AudiobookGenerator(Qwen3TTS())
    return _generator


def get_queue() -> GenerationQueue:
    """Return the process-local generation queue singleton."""

    global _queue
    if _queue is None:
        _queue = GenerationQueue(max_workers=1)
    return _queue


async def ensure_queue_started(db: Session) -> GenerationQueue:
    """Start the generation queue using the current session bind when needed."""

    queue = get_queue()
    session_factory = sessionmaker(
        bind=db.get_bind(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    await queue.start(session_factory, get_generator())
    return queue


async def shutdown_generation_runtime() -> None:
    """Stop queue workers and release the cached generator."""

    global _generator, _queue

    if _queue is not None:
        await _queue.stop()
        _queue = None

    if _generator is not None:
        _generator.close()
        _generator = None


def _serialize_job(job: JobInfo) -> JobStatusResponse:
    """Convert in-memory queue job state into an API response model."""

    return JobStatusResponse(
        job_id=job.job_id,
        book_id=job.book_id,
        chapter_id=job.chapter_id,
        status=job.status.value,
        progress=job.progress,
        error_message=job.error_message,
    )


def _validate_book_ready_for_generation(book: Book | None, chapters: list[Chapter], book_id: int) -> None:
    """Raise an HTTP error when a book cannot be queued for generation."""

    if book is None:
        raise HTTPException(status_code=404, detail=f"Book {book_id} not found")

    if book.status != BookStatus.PARSED:
        raise HTTPException(
            status_code=400,
            detail=f"Book must be parsed first. Current status: {book.status}",
        )

    if not chapters:
        raise HTTPException(status_code=400, detail="Book has no chapters")

    missing_text = [chapter.number for chapter in chapters if not (chapter.text_content or "").strip()]
    if missing_text:
        raise HTTPException(
            status_code=400,
            detail=f"Chapters missing text content: {missing_text}",
        )


@router.post("/book/{book_id}/generate", response_model=QueueJobResponse)
async def generate_book(
    book_id: int,
    request: GenerationRequest,
    db: Session = Depends(get_db),
) -> QueueJobResponse:
    """Queue a full parsed book for audio generation."""

    del request

    book = db.query(Book).filter(Book.id == book_id).first()
    chapters = db.query(Chapter).filter(Chapter.book_id == book_id).order_by(Chapter.number).all()
    _validate_book_ready_for_generation(book, chapters, book_id)

    try:
        queue = await ensure_queue_started(db)
        job_id = await queue.enqueue_book(book_id, db)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to enqueue book %s for generation", book_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return QueueJobResponse(
        job_id=job_id,
        status="queued",
        book_id=book_id,
        message=f"Book {book_id} queued for generation",
    )


@router.post("/book/{book_id}/chapter/{chapter_number}/generate", response_model=QueueJobResponse)
async def generate_chapter(
    book_id: int,
    chapter_number: int,
    db: Session = Depends(get_db),
) -> QueueJobResponse:
    """Queue a single chapter for generation."""

    chapter = (
        db.query(Chapter)
        .filter(Chapter.book_id == book_id, Chapter.number == chapter_number)
        .first()
    )
    if chapter is None:
        raise HTTPException(status_code=404, detail=f"Chapter {chapter_number} not found in book {book_id}")
    if not (chapter.text_content or "").strip():
        raise HTTPException(status_code=400, detail="Chapter has no text content")

    try:
        queue = await ensure_queue_started(db)
        job_id = await queue.enqueue_chapter(book_id, chapter_number, db)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to enqueue book %s chapter %s", book_id, chapter_number)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return QueueJobResponse(
        job_id=job_id,
        status="queued",
        book_id=book_id,
        chapter_number=chapter_number,
        message=f"Chapter {chapter_number} queued for generation",
    )


@router.get("/job/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: int, db: Session = Depends(get_db)) -> JobStatusResponse:
    """Return the current status of a generation job."""

    job = await get_queue().get_job_status(job_id, db)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return _serialize_job(job)


@router.delete("/job/{job_id}", response_model=CancelJobResponse)
async def cancel_job(job_id: int, db: Session = Depends(get_db)) -> CancelJobResponse:
    """Cancel a queued or running generation job."""

    cancelled = await get_queue().cancel_job(job_id, db)
    if not cancelled:
        raise HTTPException(status_code=400, detail="Job cannot be cancelled")

    return CancelJobResponse(
        cancelled=True,
        job_id=job_id,
        message="Job cancelled",
    )


@router.get("/book/{book_id}/chapter/{chapter_number}/audio")
async def get_chapter_audio(book_id: int, chapter_number: int, db: Session = Depends(get_db)) -> FileResponse:
    """Stream a generated chapter WAV file."""

    chapter = (
        db.query(Chapter)
        .filter(Chapter.book_id == book_id, Chapter.number == chapter_number)
        .first()
    )
    if chapter is None:
        raise HTTPException(status_code=404, detail=f"Chapter {chapter_number} not found in book {book_id}")
    if not chapter.audio_path:
        raise HTTPException(status_code=404, detail="Audio not yet generated")

    outputs_root = Path(settings.OUTPUTS_PATH).resolve()
    audio_file = (outputs_root / chapter.audio_path).resolve()
    if outputs_root not in audio_file.parents:
        raise HTTPException(status_code=400, detail="Invalid audio path")
    if not audio_file.exists():
        raise HTTPException(status_code=404, detail="Audio file not found")

    return FileResponse(audio_file, media_type="audio/wav", filename=audio_file.name)
