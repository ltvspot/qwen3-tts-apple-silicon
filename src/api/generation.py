"""Generation endpoints for queueing, monitoring, and serving chapter audio."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.api.cache import invalidate_library_cache
from src.config import settings
from src.api.generation_runtime import ensure_queue_started, get_generator, get_queue
from src.database import (
    Book,
    BookGenerationStatus,
    BookStatus,
    Chapter,
    ChapterQARecord,
    ChapterStatus,
    GenerationJob,
    GenerationJobStatus,
    QAStatus,
    get_db,
)
from src.pipeline.manuscript_validator import ManuscriptValidator
from src.pipeline.queue_manager import DuplicateGenerationJobError, JobInfo, QueueDrainingError
from src.startup import repair_invalid_generation_job_statuses

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["generation"])

ACTIVE_JOB_STATUSES = (
    GenerationJobStatus.QUEUED,
    GenerationJobStatus.RUNNING,
)

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


class ChapterGenerationStatusResponse(BaseModel):
    """Polling payload for an individual chapter."""

    chapter_n: int
    status: str
    progress_seconds: float | None = None
    expected_total_seconds: float | None = None
    generated_at: datetime | None = None
    audio_duration_seconds: float | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    audio_file_size_bytes: int | None = None
    generation_seconds: float | None = None
    current_chunk: int | None = None
    total_chunks: int | None = None


class BookGenerationStatusResponse(BaseModel):
    """Polling payload for the book-level generation panel."""

    book_id: int
    status: str
    chapters: list[ChapterGenerationStatusResponse]
    current_chapter_n: int | None = None
    current_chunk: int | None = None
    total_chunks: int | None = None
    eta_seconds: int | None = None
    started_at: datetime | None = None


class BookResetResponse(BaseModel):
    """Response returned when a book's generation state is fully reset."""

    book_id: int
    reset_chapters: int
    cancelled_jobs: list[int]
    message: str


class MissingQAChapterResponse(BaseModel):
    """Recovery payload for generated chapters missing persisted QA records."""

    chapter_n: int
    title: str | None
    status: str
    audio_path: str | None
    generated_at: datetime | None = None
    reason: str

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


def _validate_book_ready_for_generation(
    book: Book | None,
    chapters: list[Chapter],
    book_id: int,
    *,
    force: bool,
) -> None:
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

    validation_report = ManuscriptValidator.validate(
        book_id,
        [
            {
                "book_title": book.title,
                "number": chapter.number,
                "text": chapter.text_content or "",
            }
            for chapter in chapters
        ],
    )
    if not validation_report.ready_for_generation:
        error_count = sum(1 for issue in validation_report.issues if issue.severity == "error")
        raise HTTPException(
            status_code=400,
            detail=(
                f"Manuscript validation failed with {error_count} blocking issue(s). "
                f"Review /api/book/{book_id}/validate-manuscript before generating."
            ),
        )

    if not force and all(chapter.status == ChapterStatus.GENERATED for chapter in chapters):
        raise HTTPException(status_code=400, detail="All chapters are already generated.")


def _load_book_or_404(book_id: int, db: Session) -> Book:
    """Return a book or raise 404."""

    book = db.query(Book).filter(Book.id == book_id).first()
    if book is None:
        raise HTTPException(status_code=404, detail=f"Book {book_id} not found")
    return book


def _load_chapter_or_404(book_id: int, chapter_number: int, db: Session) -> Chapter:
    """Return a chapter for a book or raise 404."""

    chapter = (
        db.query(Chapter)
        .filter(Chapter.book_id == book_id, Chapter.number == chapter_number)
        .first()
    )
    if chapter is None:
        raise HTTPException(status_code=404, detail=f"Chapter {chapter_number} not found in book {book_id}")
    return chapter


def _first_incomplete_chapter_number(chapters: list[Chapter]) -> int | None:
    """Return the first chapter number that still needs generation."""

    first_incomplete = next(
        (chapter.number for chapter in chapters if chapter.status != ChapterStatus.GENERATED),
        None,
    )
    return first_incomplete


def _get_book_chapters(book_id: int, db: Session) -> list[Chapter]:
    """Return ordered chapters for a book."""

    return db.query(Chapter).filter(Chapter.book_id == book_id).order_by(Chapter.number, Chapter.id).all()


def _get_book_jobs(book_id: int, db: Session) -> list[GenerationJob]:
    """Return generation jobs for a book ordered by creation time."""

    def load_jobs() -> list[GenerationJob]:
        return (
            db.query(GenerationJob)
            .filter(GenerationJob.book_id == book_id)
            .order_by(GenerationJob.created_at.desc(), GenerationJob.id.desc())
            .all()
        )

    try:
        return load_jobs()
    except LookupError:
        logger.warning("Repairing invalid generation job statuses while loading book %s status", book_id, exc_info=True)
        db.rollback()
        repaired = repair_invalid_generation_job_statuses(db, book_id=book_id)
        if repaired:
            db.commit()
            return load_jobs()
        logger.exception("Unable to repair invalid generation job statuses for book %s", book_id)
        return []


def _reset_chapter_state(book_id: int, chapter: Chapter) -> None:
    """Reset one chapter and delete any generated artifacts."""

    generator = get_generator()
    delete_artifacts = getattr(generator, "delete_chapter_artifacts", None)
    if callable(delete_artifacts):
        delete_artifacts(book_id, chapter)
    chapter.status = ChapterStatus.PENDING
    chapter.audio_path = None
    chapter.duration_seconds = None
    chapter.qa_status = QAStatus.NOT_REVIEWED
    chapter.qa_notes = None
    chapter.started_at = None
    chapter.completed_at = None
    chapter.error_message = None
    chapter.audio_file_size_bytes = None
    chapter.current_chunk = None
    chapter.total_chunks = None
    chapter.chunk_boundaries = None
    chapter.generation_metadata = None
    chapter.mastered = False


def _latest_resume_checkpoint(book_id: int, db: Session) -> int:
    """Return the most recent persisted checkpoint for a resumable full-book job."""

    latest_job = (
        db.query(GenerationJob)
        .filter(
            GenerationJob.book_id == book_id,
            GenerationJob.chapter_id.is_(None),
            GenerationJob.status.in_(
                (
                    GenerationJobStatus.FAILED,
                    GenerationJobStatus.PAUSED,
                    GenerationJobStatus.CANCELLED,
                )
            ),
        )
        .order_by(GenerationJob.created_at.desc(), GenerationJob.id.desc())
        .first()
    )
    if latest_job is None:
        return 0
    return latest_job.last_completed_chapter


def _expected_total_seconds(chapter: Chapter) -> float | None:
    """Estimate total generation time for a chapter from its word count."""

    if not chapter.word_count:
        return None
    return round(chapter.word_count * 0.4, 1)


def _generation_seconds(chapter: Chapter) -> float | None:
    """Return the observed generation runtime for a chapter."""

    if chapter.started_at is None or chapter.completed_at is None:
        return None

    elapsed = (chapter.completed_at - chapter.started_at).total_seconds()
    if elapsed < 0:
        return None
    return round(elapsed, 1)


def _chapter_api_status(chapter: Chapter) -> str:
    """Translate ORM chapter status into the prompt-09 API contract."""

    if chapter.status == ChapterStatus.GENERATED:
        return "completed"
    if chapter.status == ChapterStatus.GENERATED_NO_QA:
        return "error"
    if chapter.status == ChapterStatus.FAILED:
        return "error"
    return chapter.status.value


def _choose_active_book_job(jobs: list[GenerationJob]) -> GenerationJob | None:
    """Prefer the running full-book job, then the newest queued one."""

    running = next(
        (
            job for job in jobs
            if job.chapter_id is None and job.status == GenerationJobStatus.RUNNING
        ),
        None,
    )
    if running is not None:
        return running

    return next(
        (
            job for job in jobs
            if job.chapter_id is None and job.status == GenerationJobStatus.QUEUED
        ),
        None,
    )


def _active_chapter_job_map(jobs: list[GenerationJob]) -> dict[int, GenerationJob]:
    """Return the active queued/running job per chapter, preferring running jobs."""

    by_chapter_id: dict[int, GenerationJob] = {}
    for status in (GenerationJobStatus.RUNNING, GenerationJobStatus.QUEUED):
        for job in jobs:
            if job.chapter_id is None or job.status != status or job.chapter_id in by_chapter_id:
                continue
            by_chapter_id[job.chapter_id] = job
    return by_chapter_id


def _chapter_progress_fraction(
    chapter: Chapter,
    ordered_chapters: list[Chapter],
    book_job: GenerationJob | None,
    chapter_job: GenerationJob | None,
) -> float | None:
    """Resolve the best available progress fraction for a generating chapter."""

    if chapter_job is not None and chapter_job.status == GenerationJobStatus.RUNNING:
        return max(0.0, min(chapter_job.progress / 100.0, 1.0))

    if book_job is None or book_job.status != GenerationJobStatus.RUNNING:
        return None

    try:
        chapter_index = next(index for index, candidate in enumerate(ordered_chapters) if candidate.id == chapter.id)
    except StopIteration:
        return None

    completed_before = sum(
        1 for candidate in ordered_chapters[:chapter_index] if candidate.status == ChapterStatus.GENERATED
    )
    chapter_progress = ((book_job.progress / 100.0) * len(ordered_chapters)) - completed_before
    return max(0.0, min(chapter_progress, 1.0))


def _serialize_chapter_generation_status(
    chapter: Chapter,
    ordered_chapters: list[Chapter],
    book_job: GenerationJob | None,
    chapter_job_map: dict[int, GenerationJob],
) -> ChapterGenerationStatusResponse:
    """Build the prompt-09 polling shape for a single chapter."""

    expected_total_seconds = _expected_total_seconds(chapter)
    progress_seconds = None
    if chapter.status == ChapterStatus.GENERATING:
        progress_fraction = _chapter_progress_fraction(
            chapter,
            ordered_chapters,
            book_job,
            chapter_job_map.get(chapter.id),
        )
        if progress_fraction is not None and expected_total_seconds is not None:
            progress_seconds = round(expected_total_seconds * progress_fraction, 1)

    return ChapterGenerationStatusResponse(
        chapter_n=chapter.number,
        status=_chapter_api_status(chapter),
        progress_seconds=progress_seconds,
        expected_total_seconds=expected_total_seconds,
        generated_at=(
            chapter.completed_at
            if chapter.status in {ChapterStatus.GENERATED, ChapterStatus.GENERATED_NO_QA}
            else None
        ),
        audio_duration_seconds=(
            chapter.duration_seconds
            if chapter.status in {ChapterStatus.GENERATED, ChapterStatus.GENERATED_NO_QA}
            else None
        ),
        error_message=chapter.error_message,
        started_at=chapter.started_at,
        audio_file_size_bytes=chapter.audio_file_size_bytes,
        generation_seconds=_generation_seconds(chapter),
        current_chunk=chapter.current_chunk,
        total_chunks=chapter.total_chunks,
    )


def _calculate_eta_seconds(
    ordered_chapters: list[Chapter],
    chapter_statuses: list[ChapterGenerationStatusResponse],
    *,
    is_generating: bool,
) -> int | None:
    """Estimate remaining wall-clock time for the current generation run."""

    if not is_generating:
        return None

    completed_generation_times = [
        generation_seconds
        for chapter in ordered_chapters
        if chapter.status == ChapterStatus.GENERATED
        for generation_seconds in [_generation_seconds(chapter)]
        if generation_seconds is not None
    ]
    completed_average = None
    if completed_generation_times:
        completed_average = sum(completed_generation_times) / len(completed_generation_times)

    remaining_seconds = 0.0
    for chapter, chapter_status in zip(ordered_chapters, chapter_statuses, strict=False):
        if chapter_status.status == "completed":
            continue

        expected_total = chapter_status.expected_total_seconds
        if chapter_status.status == "generating":
            if expected_total is not None and chapter_status.progress_seconds is not None:
                remaining_seconds += max(expected_total - chapter_status.progress_seconds, 0.0)
            elif completed_average is not None:
                remaining_seconds += completed_average
        else:
            if expected_total is not None:
                remaining_seconds += expected_total
            elif completed_average is not None:
                remaining_seconds += completed_average

    return int(round(remaining_seconds))


def _build_book_status(book: Book, db: Session) -> BookGenerationStatusResponse:
    """Return the aggregate prompt-09 polling payload for a book."""

    chapters = _get_book_chapters(book.id, db)
    jobs = _get_book_jobs(book.id, db)
    active_jobs = [job for job in jobs if job.status in ACTIVE_JOB_STATUSES]
    book_job = _choose_active_book_job(active_jobs)
    chapter_job_map = _active_chapter_job_map(active_jobs)
    chapter_statuses = [
        _serialize_chapter_generation_status(chapter, chapters, book_job, chapter_job_map)
        for chapter in chapters
    ]

    generating_chapter = next((chapter for chapter in chapters if chapter.status == ChapterStatus.GENERATING), None)
    queued_chapter = None
    if generating_chapter is None:
        for job in active_jobs:
            if job.chapter_id is None:
                continue
            queued_chapter = next((chapter for chapter in chapters if chapter.id == job.chapter_id), None)
            if queued_chapter is not None:
                break

    is_generating = bool(active_jobs or generating_chapter is not None)
    has_error = any(
        chapter.status in {ChapterStatus.FAILED, ChapterStatus.GENERATED_NO_QA}
        for chapter in chapters
    )

    if is_generating:
        status = "generating"
    elif has_error or book.generation_status == BookGenerationStatus.ERROR:
        status = "error"
    else:
        status = "idle"

    current_chapter_n = None
    current_chunk = None
    total_chunks = None
    if generating_chapter is not None:
        current_chapter_n = generating_chapter.number
        current_chunk = generating_chapter.current_chunk
        total_chunks = generating_chapter.total_chunks
    elif queued_chapter is not None:
        current_chapter_n = queued_chapter.number
        current_chunk = queued_chapter.current_chunk
        total_chunks = queued_chapter.total_chunks
    elif book_job is not None:
        next_incomplete = next(
            (chapter.number for chapter in chapters if chapter.status != ChapterStatus.GENERATED),
            None,
        )
        current_chapter_n = next_incomplete

    started_at = book.generation_started_at
    if started_at is None:
        started_at = next((job.started_at for job in active_jobs if job.started_at is not None), None)

    return BookGenerationStatusResponse(
        book_id=book.id,
        status=status,
        chapters=chapter_statuses,
        current_chapter_n=current_chapter_n,
        current_chunk=current_chunk,
        total_chunks=total_chunks,
        eta_seconds=_calculate_eta_seconds(chapters, chapter_statuses, is_generating=status == "generating"),
        started_at=started_at,
    )


async def _enqueue_book_generation(book_id: int, db: Session, *, force: bool) -> QueueJobResponse:
    """Validate and queue a full-book generation job."""

    book = _load_book_or_404(book_id, db)
    chapters = _get_book_chapters(book_id, db)
    _validate_book_ready_for_generation(book, chapters, book_id, force=force)

    try:
        queue = await ensure_queue_started(db)
        job_id = await queue.enqueue_book(book_id, db, force=force)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DuplicateGenerationJobError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except QueueDrainingError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to enqueue book %s for generation", book_id)
        raise HTTPException(status_code=500, detail="Failed to queue generation job.") from exc

    invalidate_library_cache()
    return QueueJobResponse(
        job_id=job_id,
        status="queued",
        book_id=book_id,
        message=f"Book {book_id} queued for generation",
    )


@router.post("/book/{book_id}/generate", response_model=QueueJobResponse)
async def generate_book(
    book_id: int,
    force: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> QueueJobResponse:
    """Queue a full parsed book for audio generation."""

    return await _enqueue_book_generation(book_id, db, force=force)


@router.post("/book/{book_id}/generate-all", response_model=QueueJobResponse)
async def generate_book_remaining(
    book_id: int,
    force: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> QueueJobResponse:
    """Queue generation for the remaining chapters in a parsed book."""

    return await _enqueue_book_generation(book_id, db, force=force)


@router.post("/book/{book_id}/resume", response_model=QueueJobResponse)
async def resume_book_generation(book_id: int, db: Session = Depends(get_db)) -> QueueJobResponse:
    """Resume generation for the first chapter that still needs audio."""

    book = _load_book_or_404(book_id, db)
    chapters = _get_book_chapters(book_id, db)
    _validate_book_ready_for_generation(book, chapters, book_id, force=False)

    resume_from = _first_incomplete_chapter_number(chapters)
    if resume_from is None:
        raise HTTPException(status_code=400, detail="All chapters are already generated.")
    resume_checkpoint = max(_latest_resume_checkpoint(book_id, db), max(resume_from - 1, 0))

    try:
        queue = await ensure_queue_started(db)
        job_id = await queue.enqueue_book(
            book_id,
            db,
            force=False,
            last_completed_chapter=resume_checkpoint,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DuplicateGenerationJobError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except QueueDrainingError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to resume generation for book %s", book_id)
        raise HTTPException(status_code=500, detail="Failed to resume generation.") from exc

    invalidate_library_cache()
    return QueueJobResponse(
        job_id=job_id,
        status="queued",
        book_id=book_id,
        chapter_number=resume_from,
        message=f"Generation resumed from chapter {resume_from}",
    )


@router.post("/book/{book_id}/chapter/{chapter_number}/generate", response_model=QueueJobResponse)
async def generate_chapter(
    book_id: int,
    chapter_number: int,
    force: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> QueueJobResponse:
    """Queue a single chapter for generation."""

    book = _load_book_or_404(book_id, db)
    if book.status != BookStatus.PARSED:
        raise HTTPException(
            status_code=400,
            detail=f"Book must be parsed first. Current status: {book.status}",
        )

    chapter = _load_chapter_or_404(book_id, chapter_number, db)
    if not (chapter.text_content or "").strip():
        raise HTTPException(status_code=400, detail="Chapter has no text content")
    if chapter.status == ChapterStatus.GENERATED and not force:
        raise HTTPException(
            status_code=400,
            detail="Chapter audio already exists. Use force=true to re-generate it.",
        )
    try:
        queue = await ensure_queue_started(db)
        job_id = await queue.enqueue_chapter(book_id, chapter_number, db, force=force)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DuplicateGenerationJobError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except QueueDrainingError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to enqueue book %s chapter %s", book_id, chapter_number)
        raise HTTPException(status_code=500, detail="Failed to queue chapter generation.") from exc

    invalidate_library_cache()
    return QueueJobResponse(
        job_id=job_id,
        status="queued",
        book_id=book_id,
        chapter_number=chapter_number,
        message=f"Chapter {chapter_number} queued for generation",
    )


@router.post("/book/{book_id}/chapter/{chapter_number}/resume", response_model=QueueJobResponse)
async def resume_chapter_generation(
    book_id: int,
    chapter_number: int,
    db: Session = Depends(get_db),
) -> QueueJobResponse:
    """Resume one chapter from its saved chunk checkpoints when available."""

    book = _load_book_or_404(book_id, db)
    if book.status != BookStatus.PARSED:
        raise HTTPException(
            status_code=400,
            detail=f"Book must be parsed first. Current status: {book.status}",
        )

    chapter = _load_chapter_or_404(book_id, chapter_number, db)
    if not (chapter.text_content or "").strip():
        raise HTTPException(status_code=400, detail="Chapter has no text content")
    if chapter.status == ChapterStatus.GENERATED:
        raise HTTPException(status_code=400, detail="Chapter audio already exists.")
    try:
        queue = await ensure_queue_started(db)
        job_id = await queue.enqueue_chapter(book_id, chapter_number, db, force=False)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DuplicateGenerationJobError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except QueueDrainingError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to resume book %s chapter %s", book_id, chapter_number)
        raise HTTPException(status_code=500, detail="Failed to resume chapter generation.") from exc

    invalidate_library_cache()
    return QueueJobResponse(
        job_id=job_id,
        status="queued",
        book_id=book_id,
        chapter_number=chapter_number,
        message=f"Chapter {chapter_number} resumed from saved chunk checkpoints",
    )


@router.get("/books/{book_id}/chapters/missing-qa", response_model=list[MissingQAChapterResponse])
def get_missing_qa_chapters(book_id: int, db: Session = Depends(get_db)) -> list[MissingQAChapterResponse]:
    """Return generated chapters whose QA rows are missing and need recovery."""

    _load_book_or_404(book_id, db)
    chapters = _get_book_chapters(book_id, db)
    qa_numbers = {
        chapter_n
        for (chapter_n,) in (
            db.query(ChapterQARecord.chapter_n)
            .filter(ChapterQARecord.book_id == book_id)
            .all()
        )
    }

    missing: list[MissingQAChapterResponse] = []
    for chapter in chapters:
        if chapter.status not in {ChapterStatus.GENERATED, ChapterStatus.GENERATED_NO_QA}:
            continue
        if chapter.number in qa_numbers:
            continue
        missing.append(
            MissingQAChapterResponse(
                chapter_n=chapter.number,
                title=chapter.title,
                status=chapter.status.value,
                audio_path=chapter.audio_path,
                generated_at=chapter.completed_at,
                reason="Generated chapter has no QA record.",
            )
        )
    return missing


@router.post("/book/{book_id}/reset", response_model=BookResetResponse)
async def reset_book_generation_state(book_id: int, db: Session = Depends(get_db)) -> BookResetResponse:
    """Return a book to a clean pre-generation state."""

    book = _load_book_or_404(book_id, db)
    chapters = _get_book_chapters(book_id, db)
    queue = await ensure_queue_started(db)
    active_jobs = (
        db.query(GenerationJob)
        .filter(
            GenerationJob.book_id == book_id,
            GenerationJob.status.in_(
                (
                    GenerationJobStatus.QUEUED,
                    GenerationJobStatus.RUNNING,
                    GenerationJobStatus.PAUSED,
                )
            ),
        )
        .all()
    )

    cancelled_jobs: list[int] = []
    for job in active_jobs:
        forced = await queue.force_cancel_job(job.id, db, reason="Book reset requested by operator.")
        if forced is not None:
            cancelled_jobs.append(job.id)

    for chapter in chapters:
        _reset_chapter_state(book_id, chapter)

    book.status = BookStatus.PARSED
    book.generation_status = BookGenerationStatus.IDLE
    book.generation_started_at = None
    book.generation_eta_seconds = None
    book.current_job_id = None
    db.commit()
    invalidate_library_cache()
    return BookResetResponse(
        book_id=book.id,
        reset_chapters=len(chapters),
        cancelled_jobs=cancelled_jobs,
        message="Book generation state reset and ready for regeneration",
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
    """Cancel a queued or running job."""

    cancelled = await get_queue().cancel_job(job_id, db)
    if not cancelled:
        raise HTTPException(status_code=400, detail="Job cannot be cancelled")

    invalidate_library_cache()
    return CancelJobResponse(
        cancelled=True,
        job_id=job_id,
        message="Job cancelled",
    )


@router.get("/book/{book_id}/status", response_model=BookGenerationStatusResponse)
async def get_book_generation_status(book_id: int, db: Session = Depends(get_db)) -> BookGenerationStatusResponse:
    """Return prompt-09 generation progress for a book."""

    book = _load_book_or_404(book_id, db)
    return _build_book_status(book, db)


@router.get(
    "/book/{book_id}/chapter/{chapter_number}/status",
    response_model=ChapterGenerationStatusResponse,
)
async def get_chapter_generation_status(
    book_id: int,
    chapter_number: int,
    db: Session = Depends(get_db),
) -> ChapterGenerationStatusResponse:
    """Return prompt-09 generation progress for one chapter."""

    book = _load_book_or_404(book_id, db)
    chapter = _load_chapter_or_404(book_id, chapter_number, db)
    book_status = _build_book_status(book, db)
    chapter_status = next(
        (candidate for candidate in book_status.chapters if candidate.chapter_n == chapter.number),
        None,
    )
    if chapter_status is None:
        raise HTTPException(status_code=404, detail=f"Chapter {chapter_number} not found in book {book_id}")
    return chapter_status


@router.get("/book/{book_id}/chapter/{chapter_number}/audio")
async def get_chapter_audio(book_id: int, chapter_number: int, db: Session = Depends(get_db)) -> FileResponse:
    """Stream a generated chapter WAV file."""

    chapter = _load_chapter_or_404(book_id, chapter_number, db)
    if not chapter.audio_path:
        raise HTTPException(status_code=404, detail="Audio not yet generated")

    outputs_root = Path(settings.OUTPUTS_PATH).resolve()
    audio_file = (outputs_root / chapter.audio_path).resolve()
    if outputs_root not in audio_file.parents:
        raise HTTPException(status_code=400, detail="Invalid audio path")
    if not audio_file.exists():
        raise HTTPException(status_code=404, detail="Audio file not found")

    return FileResponse(
        audio_file,
        media_type="audio/wav",
        filename=audio_file.name,
        headers={
            "Accept-Ranges": "bytes",
            "Cache-Control": "no-cache",
        },
    )
