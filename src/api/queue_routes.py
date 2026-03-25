"""Queue management endpoints for production generation jobs."""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, selectinload

from src.api.cache import invalidate_library_cache
from src.api.generation_runtime import ensure_queue_started, get_queue
from src.config import get_application_settings
from src.database import (
    Book,
    BookStatus,
    Chapter,
    ChapterStatus,
    GenerationJob,
    GenerationJobStatus,
    GenerationJobType,
    JobHistory,
    get_db,
    utc_now,
)

router = APIRouter(prefix="/api/queue", tags=["queue"])

TERMINAL_STATUSES = {
    GenerationJobStatus.COMPLETED,
    GenerationJobStatus.FAILED,
    GenerationJobStatus.CANCELLED,
}
ACTIVE_STATUSES = {
    GenerationJobStatus.QUEUED,
    GenerationJobStatus.RUNNING,
    GenerationJobStatus.PAUSED,
}


class QueueJobListItem(BaseModel):
    """Summary payload for queue list rows."""

    job_id: int
    book_id: int
    book_title: str
    book_author: str
    job_type: str
    status: str
    priority: int
    chapters_total: int
    chapters_completed: int
    chapters_failed: int
    current_chapter_n: int | None
    current_chapter_title: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    paused_at: datetime | None = None
    completed_at: datetime | None = None
    eta_seconds: int | None = None
    avg_seconds_per_chapter: float | None = None
    error_message: str | None = None
    progress_percent: float


class QueueStatsPayload(BaseModel):
    """Aggregate queue statistics."""

    total_books_in_queue: int
    total_chapters: int
    estimated_total_time_seconds: int | None = None


class QueueListResponse(BaseModel):
    """Response payload for the queue list."""

    jobs: list[QueueJobListItem]
    total_count: int
    active_job_count: int
    queue_stats: QueueStatsPayload


class ChapterBreakdownItem(BaseModel):
    """Detailed chapter status for a job."""

    chapter_n: int
    chapter_title: str | None = None
    status: str
    duration_seconds: float | None = None
    completed_at: datetime | None = None
    progress_seconds: float | None = None
    expected_total_seconds: float | None = None
    started_at: datetime | None = None
    error_message: str | None = None


class JobHistoryItem(BaseModel):
    """Serialized job history row."""

    action: str
    details: str | None = None
    timestamp: datetime


class QueueJobDetailResponse(BaseModel):
    """Detailed queue payload for a single job."""

    job_id: int
    book_id: int
    book_title: str
    status: str
    priority: int
    chapters_total: int
    chapters_completed: int
    chapters_failed: int
    current_chapter_n: int | None = None
    created_at: datetime
    started_at: datetime | None = None
    paused_at: datetime | None = None
    completed_at: datetime | None = None
    eta_seconds: int | None = None
    avg_seconds_per_chapter: float | None = None
    error_message: str | None = None
    chapter_breakdown: list[ChapterBreakdownItem]
    history: list[JobHistoryItem]


class QueueControlRequest(BaseModel):
    """Request body for pause and cancel operations."""

    reason: str | None = None


class PauseJobResponse(BaseModel):
    """Pause endpoint response."""

    job_id: int
    status: str
    paused_at: datetime | None = None


class ResumeJobResponse(BaseModel):
    """Resume endpoint response."""

    job_id: int
    status: str
    paused_at: datetime | None = None


class CancelJobResponse(BaseModel):
    """Cancel endpoint response."""

    job_id: int
    status: str
    error_message: str


class PriorityUpdateRequest(BaseModel):
    """Priority update payload."""

    priority: int | None = Field(default=None, ge=0, le=100)
    action: str | None = None


class PriorityUpdateResponse(BaseModel):
    """Priority endpoint response."""

    job_id: int
    priority: int
    queue_position: int


class BatchAllRequest(BaseModel):
    """Batch queue payload."""

    priority: int = Field(default=0, ge=0, le=100)
    voice: str | None = None
    emotion: str | None = None
    speed: float | None = Field(default=None, ge=0.5, le=2.0)


class BatchAllResponse(BaseModel):
    """Batch queue response."""

    jobs_created: int
    books_queued: int
    total_chapters: int
    estimated_completion_seconds: int | None = None
    message: str


def _queue_status(job: GenerationJob) -> str:
    """Map persisted job state into the prompt-10 status vocabulary."""

    if job.status == GenerationJobStatus.RUNNING:
        return "paused" if job.pause_requested else "generating"
    if job.status == GenerationJobStatus.QUEUED:
        return "queued"
    if job.status == GenerationJobStatus.PAUSED:
        return "paused"
    if job.status == GenerationJobStatus.COMPLETED:
        return "completed"
    return "error"


def _expected_generation_seconds(chapter: Chapter, avg_seconds: float | None) -> float | None:
    """Estimate generation runtime for one chapter."""

    if avg_seconds is not None:
        return round(avg_seconds, 2)
    if chapter.word_count is None:
        return None
    return round(chapter.word_count * 0.4, 1)


def _chapter_title(chapter: Chapter | None) -> str | None:
    """Return the display title for a chapter when available."""

    if chapter is None:
        return None
    return chapter.title or f"Chapter {chapter.number}"


def _current_chapter(job: GenerationJob) -> Chapter | None:
    """Resolve the active chapter for a job from loaded relationships."""

    if job.chapter is not None:
        return job.chapter
    if job.book is None or job.current_chapter_n is None:
        return None
    return next((chapter for chapter in job.book.chapters if chapter.number == job.current_chapter_n), None)


def _progress_percent(job: GenerationJob) -> float:
    """Return queue list progress based on completed chapters."""

    if job.chapters_total <= 0:
        return 0.0
    return round((job.chapters_completed / job.chapters_total) * 100, 2)


def _remaining_chapters(job: GenerationJob) -> int:
    """Return chapters still remaining in the queue."""

    return max(job.chapters_total - job.chapters_completed - job.chapters_failed, 0)


def _job_sort_key(job: GenerationJob) -> tuple[int, int, datetime, int]:
    """Sort jobs with active generation first, then queue priority."""

    status_order = {
        "generating": 0,
        "queued": 1,
        "paused": 2,
        "error": 3,
        "completed": 4,
    }
    return (
        status_order.get(_queue_status(job), 9),
        -job.priority,
        job.created_at,
        job.id,
    )


def _include_recent_job(job: GenerationJob) -> bool:
    """Return True when the job should appear in the queue list."""

    if job.status not in TERMINAL_STATUSES:
        return True
    recent_cutoff = utc_now() - timedelta(days=7)
    completed_at = job.completed_at or job.created_at
    return completed_at >= recent_cutoff


def _serialize_queue_job(job: GenerationJob) -> QueueJobListItem:
    """Convert one ORM job into the list response shape."""

    current_chapter = _current_chapter(job)
    return QueueJobListItem(
        job_id=job.id,
        book_id=job.book_id,
        book_title=job.book.title if job.book is not None else f"Book {job.book_id}",
        book_author=job.book.author if job.book is not None else "Unknown Author",
        job_type=job.job_type.value if isinstance(job.job_type, GenerationJobType) else str(job.job_type),
        status=_queue_status(job),
        priority=job.priority,
        chapters_total=job.chapters_total,
        chapters_completed=job.chapters_completed,
        chapters_failed=job.chapters_failed,
        current_chapter_n=job.current_chapter_n,
        current_chapter_title=_chapter_title(current_chapter),
        created_at=job.created_at,
        started_at=job.started_at,
        paused_at=job.paused_at,
        completed_at=job.completed_at,
        eta_seconds=job.eta_seconds,
        avg_seconds_per_chapter=job.avg_seconds_per_chapter,
        error_message=job.error_message,
        progress_percent=_progress_percent(job),
    )


def _chapter_breakdown_status(job: GenerationJob, chapter: Chapter) -> str:
    """Resolve per-chapter status for the job detail view."""

    if chapter.status == ChapterStatus.GENERATED:
        return "completed"
    if chapter.status == ChapterStatus.FAILED:
        return "error"
    if job.current_chapter_n == chapter.number and job.status == GenerationJobStatus.RUNNING:
        return "generating"
    if job.current_chapter_n == chapter.number and job.status == GenerationJobStatus.PAUSED:
        return "paused"
    if job.current_chapter_n == chapter.number and job.pause_requested:
        return "paused"
    return "queued"


def _serialize_chapter_breakdown(job: GenerationJob) -> list[ChapterBreakdownItem]:
    """Return the per-chapter detail payload for a job."""

    chapters = [job.chapter] if job.chapter is not None else list(job.book.chapters if job.book is not None else [])
    items: list[ChapterBreakdownItem] = []

    for chapter in chapters:
        if chapter is None:
            continue
        expected_total_seconds = _expected_generation_seconds(chapter, job.avg_seconds_per_chapter)
        progress_seconds = None
        if job.current_chapter_n == chapter.number and job.current_chapter_progress and expected_total_seconds is not None:
            progress_seconds = round(expected_total_seconds * (job.current_chapter_progress / 100.0), 1)

        items.append(
            ChapterBreakdownItem(
                chapter_n=chapter.number,
                chapter_title=_chapter_title(chapter),
                status=_chapter_breakdown_status(job, chapter),
                duration_seconds=chapter.duration_seconds,
                completed_at=chapter.completed_at,
                progress_seconds=progress_seconds,
                expected_total_seconds=expected_total_seconds,
                started_at=chapter.started_at,
                error_message=chapter.error_message,
            )
        )

    return items


def _serialize_history(entries: list[JobHistory]) -> list[JobHistoryItem]:
    """Convert job history rows into API payloads."""

    return [
        JobHistoryItem(
            action=entry.action,
            details=entry.details,
            timestamp=entry.timestamp,
        )
        for entry in entries
    ]


def _base_job_query(db: Session):
    """Return the shared ORM query used by queue endpoints."""

    return (
        db.query(GenerationJob)
        .options(
            selectinload(GenerationJob.book).selectinload(Book.chapters),
            selectinload(GenerationJob.chapter),
            selectinload(GenerationJob.history_entries),
        )
    )


@router.get("", response_model=QueueListResponse)
async def list_queue_jobs(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    status: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> QueueListResponse:
    """Return queue jobs plus aggregate queue statistics."""

    jobs = [job for job in _base_job_query(db).all() if _include_recent_job(job)]
    active_jobs = [job for job in jobs if job.status in ACTIVE_STATUSES]

    if status is not None:
        jobs = [job for job in jobs if _queue_status(job) == status]

    ordered_jobs = sorted(jobs, key=_job_sort_key)
    paginated_jobs = ordered_jobs[offset : offset + limit]

    estimated_total_time_seconds = None
    if active_jobs:
        estimated_total_time_seconds = sum(job.eta_seconds or 0 for job in active_jobs)

    queue_stats = QueueStatsPayload(
        total_books_in_queue=len(active_jobs),
        total_chapters=sum(_remaining_chapters(job) for job in active_jobs),
        estimated_total_time_seconds=estimated_total_time_seconds,
    )

    return QueueListResponse(
        jobs=[_serialize_queue_job(job) for job in paginated_jobs],
        total_count=len(ordered_jobs),
        active_job_count=len(active_jobs),
        queue_stats=queue_stats,
    )


@router.get("/{job_id}", response_model=QueueJobDetailResponse)
async def get_queue_job(job_id: int, db: Session = Depends(get_db)) -> QueueJobDetailResponse:
    """Return detailed queue information for one job."""

    job = _base_job_query(db).filter(GenerationJob.id == job_id).first()
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    return QueueJobDetailResponse(
        job_id=job.id,
        book_id=job.book_id,
        book_title=job.book.title if job.book is not None else f"Book {job.book_id}",
        status=_queue_status(job),
        priority=job.priority,
        chapters_total=job.chapters_total,
        chapters_completed=job.chapters_completed,
        chapters_failed=job.chapters_failed,
        current_chapter_n=job.current_chapter_n,
        created_at=job.created_at,
        started_at=job.started_at,
        paused_at=job.paused_at,
        completed_at=job.completed_at,
        eta_seconds=job.eta_seconds,
        avg_seconds_per_chapter=job.avg_seconds_per_chapter,
        error_message=job.error_message,
        chapter_breakdown=_serialize_chapter_breakdown(job),
        history=_serialize_history(job.history_entries),
    )


@router.post("/{job_id}/pause", response_model=PauseJobResponse)
async def pause_queue_job(
    job_id: int,
    request: QueueControlRequest,
    db: Session = Depends(get_db),
) -> PauseJobResponse:
    """Pause a queued or generating job."""

    queue = await ensure_queue_started(db)
    job = await queue.pause_job(job_id, db, reason=request.reason)
    if job is None:
        raise HTTPException(status_code=400, detail="Job cannot be paused")

    invalidate_library_cache()
    return PauseJobResponse(job_id=job.id, status="paused", paused_at=job.paused_at)


@router.post("/{job_id}/resume", response_model=ResumeJobResponse)
async def resume_queue_job(job_id: int, db: Session = Depends(get_db)) -> ResumeJobResponse:
    """Resume a paused job."""

    queue = await ensure_queue_started(db)
    job = await queue.resume_job(job_id, db)
    if job is None:
        raise HTTPException(status_code=400, detail="Job cannot be resumed")

    invalidate_library_cache()
    return ResumeJobResponse(job_id=job.id, status="queued", paused_at=job.paused_at)


@router.post("/{job_id}/cancel", response_model=CancelJobResponse)
async def cancel_queue_job(
    job_id: int,
    request: QueueControlRequest,
    db: Session = Depends(get_db),
) -> CancelJobResponse:
    """Cancel a queued, paused, or running job."""

    queue = await ensure_queue_started(db)
    cancelled = await queue.cancel_job(job_id, db, reason=request.reason)
    if not cancelled:
        raise HTTPException(status_code=400, detail="Job cannot be cancelled")

    job = db.query(GenerationJob).filter(GenerationJob.id == job_id).first()
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    invalidate_library_cache()
    return CancelJobResponse(
        job_id=job.id,
        status="error",
        error_message=job.error_message or "Job cancelled by user.",
    )


@router.put("/{job_id}/priority", response_model=PriorityUpdateResponse)
async def update_queue_priority(
    job_id: int,
    request: PriorityUpdateRequest,
    db: Session = Depends(get_db),
) -> PriorityUpdateResponse:
    """Adjust a job's queue priority."""

    queue = get_queue()
    result = await queue.update_priority(job_id, db, priority=request.priority, action=request.action)
    if result is None:
        raise HTTPException(status_code=400, detail="Priority could not be updated")

    job, queue_position = result
    return PriorityUpdateResponse(job_id=job.id, priority=job.priority, queue_position=queue_position)


@router.post("/batch-all", response_model=BatchAllResponse)
async def batch_queue_all_books(
    request: BatchAllRequest,
    db: Session = Depends(get_db),
) -> BatchAllResponse:
    """Queue all parsed books that do not already have active jobs."""

    queue = await ensure_queue_started(db)
    default_voice = get_application_settings().default_voice
    parsed_books = (
        db.query(Book)
        .options(selectinload(Book.chapters))
        .filter(Book.status == BookStatus.PARSED)
        .order_by(Book.id.asc())
        .all()
    )

    jobs_created = 0
    total_chapters = 0
    estimated_completion_seconds = 0

    for book in parsed_books:
        if not book.chapters:
            continue

        active_job = (
            db.query(GenerationJob.id)
            .filter(
                GenerationJob.book_id == book.id,
                GenerationJob.status.in_((GenerationJobStatus.QUEUED, GenerationJobStatus.RUNNING, GenerationJobStatus.PAUSED)),
            )
            .first()
        )
        if active_job is not None:
            continue

        job_id = await queue.enqueue_book(
            book.id,
            db,
            priority=request.priority,
            voice_name=request.voice or default_voice.name,
            emotion=default_voice.emotion if request.emotion is None else request.emotion,
            speed=default_voice.speed if request.speed is None else request.speed,
            job_type=GenerationJobType.BATCH_ALL,
        )
        job = db.query(GenerationJob).filter(GenerationJob.id == job_id).first()
        if job is None:
            continue

        jobs_created += 1
        total_chapters += job.chapters_total
        estimated_completion_seconds += job.eta_seconds or 0

    return BatchAllResponse(
        jobs_created=jobs_created,
        books_queued=jobs_created,
        total_chapters=total_chapters,
        estimated_completion_seconds=estimated_completion_seconds if jobs_created else 0,
        message=f"All {jobs_created} parsed books queued for generation" if jobs_created else "No parsed books were queued.",
    )
