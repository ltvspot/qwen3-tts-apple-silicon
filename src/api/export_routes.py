"""Export API routes for audiobook packaging and downloads."""

from __future__ import annotations

import atexit
import asyncio
import json
import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor, wait
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, selectinload, sessionmaker

from src.api.cache import invalidate_library_cache
from src.database import Book, BookExportStatus, Chapter, ChapterQARecord, ChapterStatus, ExportJob, SessionLocal, get_db, utc_now
from src.pipeline.exporter import (
    ExportFormatResult,
    QAReport,
    _verify_checksum,
    _empty_format_details,
    _chapter_is_approved,
    _normalize_export_formats,
    cleanup_export_temp_files,
    estimate_export_seconds,
    get_expected_export_sha256,
    get_export_output_path,
    reconcile_book_export_artifacts,
    reconcile_export_job_state,
    run_export_job_sync,
)

router = APIRouter(prefix="/api", tags=["export"])
logger = logging.getLogger(__name__)

_export_tasks: set[asyncio.Task[None]] = set()
_export_threads_lock = threading.RLock()
_export_executor: ThreadPoolExecutor | None = None
_export_futures: dict[int, Future[None]] = {}
_batch_export_lock = threading.RLock()
_batch_export_monitor_task: asyncio.Task[None] | None = None
_batch_export_progress: "BatchExportProgressResponse | None" = None


def _track_export_task(task: asyncio.Task[None]) -> asyncio.Task[None]:
    """Register one in-process export task under the shared runtime lock."""

    with _export_threads_lock:
        _export_tasks.add(task)
    return task


def _discard_export_task(task: asyncio.Task[None]) -> None:
    """Remove one tracked export task under the shared runtime lock."""

    with _export_threads_lock:
        _export_tasks.discard(task)


def _snapshot_export_tasks() -> list[asyncio.Task[None]]:
    """Return a stable snapshot of tracked export tasks."""

    with _export_threads_lock:
        return list(_export_tasks)


def _snapshot_export_threads() -> list[threading.Thread]:
    """Return a stable snapshot of tracked export threads."""

    return []


def _clear_export_threads() -> list[threading.Thread]:
    """Clear tracked export threads and return the prior snapshot."""

    return []


def _ensure_export_executor() -> ThreadPoolExecutor:
    """Return the shared export executor, recreating it after test shutdowns."""

    global _export_executor
    with _export_threads_lock:
        if _export_executor is None:
            _export_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="export")
        return _export_executor


def _snapshot_export_futures() -> dict[int, Future[None]]:
    """Return a stable snapshot of tracked export futures."""

    with _export_threads_lock:
        return dict(_export_futures)


def _clear_export_futures() -> dict[int, Future[None]]:
    """Clear tracked export futures and return the prior snapshot."""

    with _export_threads_lock:
        futures = dict(_export_futures)
        _export_futures.clear()
    return futures


def _discard_export_future(export_job_id: int, future: Future[None]) -> None:
    """Drop one completed export future if it still owns the registry slot."""

    with _export_threads_lock:
        if _export_futures.get(export_job_id) is future:
            _export_futures.pop(export_job_id, None)


def _wait_for_export_workers(timeout_seconds: float = 60.0) -> tuple[set[Future[None]], set[Future[None]]]:
    """Wait for the currently tracked export workers to finish."""

    snapshot = _snapshot_export_futures()
    if not snapshot:
        return (set(), set())
    return wait(list(snapshot.values()), timeout=timeout_seconds)


def _mark_export_job_interrupted(
    export_job_id: int,
    *,
    session_factory: sessionmaker[Session] | None = None,
) -> None:
    """Persist an interrupted export state when shutdown outlives the worker timeout."""

    session_factory = session_factory or SessionLocal
    with session_factory() as db_session:
        export_job = db_session.query(ExportJob).filter(ExportJob.id == export_job_id).first()
        if export_job is None or export_job.export_status != BookExportStatus.PROCESSING:
            return

        book = db_session.query(Book).filter(Book.id == export_job.book_id).first()
        interrupted_at = utc_now()
        export_job.export_status = BookExportStatus.ERROR
        export_job.completed_at = interrupted_at
        export_job.updated_at = interrupted_at
        export_job.current_stage = "interrupted"
        export_job.current_format = None
        export_job.error_message = "Export interrupted during shutdown."
        if book is not None:
            book.export_status = BookExportStatus.ERROR
        db_session.commit()


def _shutdown_export_workers(
    *,
    timeout_seconds: float = 60.0,
    recreate_executor: bool = False,
    session_factory: sessionmaker[Session] | None = None,
) -> None:
    """Request executor shutdown, waiting briefly before marking stuck jobs interrupted."""

    global _export_executor

    snapshot = _snapshot_export_futures()
    with _export_threads_lock:
        executor = _export_executor
        _export_executor = None

    if executor is not None:
        executor.shutdown(wait=False, cancel_futures=True)

    done: set[Future[None]] = set()
    not_done: set[Future[None]] = set()
    if snapshot:
        done, not_done = wait(list(snapshot.values()), timeout=timeout_seconds)

    for export_job_id, future in snapshot.items():
        if future.cancelled() or future in not_done:
            _mark_export_job_interrupted(export_job_id, session_factory=session_factory)
            cleanup_export_temp_files(export_job_id)

    _clear_export_futures()
    if recreate_executor:
        _ensure_export_executor()


def _clear_export_tasks() -> list[asyncio.Task[None]]:
    """Clear tracked export tasks and return the prior snapshot."""

    with _export_threads_lock:
        tasks = list(_export_tasks)
        _export_tasks.clear()
    return tasks


atexit.register(lambda: _shutdown_export_workers(timeout_seconds=60.0, recreate_executor=False))


class ExportRequest(BaseModel):
    """Request payload for starting an export."""

    formats: list[str] = Field(default_factory=lambda: ["mp3", "m4b"])
    include_only_approved: bool = True


class ExportQueuedResponse(BaseModel):
    """Response returned when an export job is queued."""

    book_id: int
    export_status: str
    job_id: str
    formats_requested: list[str]
    expected_completion_seconds: int
    started_at: datetime


class ExportStatusResponse(BaseModel):
    """Current export status for a book."""

    book_id: int
    export_status: str
    job_id: str | None = None
    formats: dict[str, ExportFormatResult]
    qa_report: QAReport | None = None
    error_message: str | None = None
    progress_percent: float = 0.0
    current_stage: str | None = None
    current_format: str | None = None
    current_chapter_n: int | None = None
    total_chapters: int | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class ExportCancelResponse(BaseModel):
    """Response returned when cancelling one export."""

    book_id: int
    export_status: str
    message: str


class BatchExportRequest(BaseModel):
    """Request payload for catalog-wide export queuing."""

    formats: list[str] = Field(default_factory=lambda: ["mp3", "m4b"])
    include_only_approved: bool = True
    skip_already_exported: bool = True


class BatchExportBookStatus(BaseModel):
    """Per-book status within the current batch export run."""

    book_id: int
    title: str
    status: str
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class BatchExportQueuedResponse(BaseModel):
    """Response returned when a batch export is queued."""

    batch_id: str
    status: str
    queued: int
    skipped: int
    not_ready: int
    started_at: datetime


class BatchExportProgressResponse(BaseModel):
    """Progress payload for the active or most recent batch export."""

    batch_id: str
    status: str
    total_books: int
    queued: int
    completed: int
    failed: int
    skipped: int
    not_ready: int
    in_progress: int
    formats_requested: list[str]
    include_only_approved: bool
    started_at: datetime | None = None
    completed_at: datetime | None = None
    books: list[BatchExportBookStatus] = Field(default_factory=list)


def _session_factory_for(db: Session) -> sessionmaker[Session]:
    """Create a detached session factory using the current request bind."""

    return sessionmaker(
        bind=db.get_bind(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )


def _serialize_format_details(export_job: ExportJob, formats_requested: list[str]) -> dict[str, ExportFormatResult]:
    """Deserialize stored per-format export state."""

    if not export_job.format_details:
        return _empty_format_details(formats_requested)

    try:
        raw_payload = json.loads(export_job.format_details)
    except json.JSONDecodeError:
        return _empty_format_details(formats_requested)

    serialized: dict[str, ExportFormatResult] = {}
    for export_format in formats_requested:
        if export_format in raw_payload:
            serialized[export_format] = ExportFormatResult.model_validate(raw_payload[export_format])
        else:
            serialized[export_format] = ExportFormatResult(status="pending")
    return serialized


def _serialize_status(book: Book, export_job: ExportJob | None) -> ExportStatusResponse:
    """Convert the stored export state into the API response payload."""

    if export_job is None:
        return ExportStatusResponse(
            book_id=book.id,
            export_status=book.export_status.value,
            formats={},
            progress_percent=100.0 if book.export_status == BookExportStatus.COMPLETED else 0.0,
            started_at=None,
            completed_at=book.last_export_date,
        )

    formats_requested = _normalize_export_formats(json.loads(export_job.formats_requested))
    qa_report = QAReport.model_validate(json.loads(export_job.qa_report)) if export_job.qa_report else None
    return ExportStatusResponse(
        book_id=book.id,
        export_status=export_job.export_status.value,
        job_id=export_job.job_token,
        formats=_serialize_format_details(export_job, formats_requested),
        qa_report=qa_report,
        error_message=export_job.error_message,
        progress_percent=export_job.progress_percent,
        current_stage=export_job.current_stage,
        current_format=export_job.current_format,
        current_chapter_n=export_job.current_chapter_n,
        total_chapters=export_job.total_chapters,
        started_at=export_job.started_at,
        completed_at=export_job.completed_at,
    )


def _reconcile_export_job_if_needed(book: Book, export_job: ExportJob | None, db: Session) -> ExportJob | None:
    """Repair stale export state before returning status or rejecting new work."""

    if export_job is not None:
        reconcile_export_job_state(db, book, export_job)
        db.refresh(book)
        db.refresh(export_job)
        return export_job

    if reconcile_book_export_artifacts(db, book):
        return db.query(ExportJob).filter(ExportJob.book_id == book.id).first()
    return None


def _load_book_or_404(book_id: int, db: Session) -> Book:
    """Load a book or raise a 404."""

    book = db.query(Book).filter(Book.id == book_id).first()
    if book is None:
        raise HTTPException(status_code=404, detail=f"Book {book_id} not found")
    return book


def _validate_export_chapters(
    chapters: list[Chapter],
    *,
    include_only_approved: bool,
    qa_records: dict[int, ChapterQARecord] | None = None,
) -> tuple[bool, str]:
    """Return whether the chapter set is export-ready and why when it is not."""

    if not chapters:
        return False, "Book has no chapters. Parse it first."

    total = len(chapters)
    generated = sum(chapter.status == ChapterStatus.GENERATED for chapter in chapters)
    if generated < total:
        return (
            False,
            f"Only {generated}/{total} chapters generated. Generate all chapters before exporting.",
        )

    if include_only_approved:
        approved = sum(
            _chapter_is_approved(chapter, qa_records.get(chapter.number) if qa_records else None)
            for chapter in chapters
        )
        if approved < total:
            return (
                False,
                f"Only {approved}/{total} chapters approved. Approve all chapters before exporting.",
            )

    return True, ""


def _validate_book_export_readiness(
    book_id: int,
    db: Session,
    *,
    include_only_approved: bool = False,
) -> tuple[bool, str]:
    """Return whether a persisted book is ready for export and why when it is not."""

    book = db.query(Book).filter(Book.id == book_id).first()
    if book is None:
        return False, "Book not found"

    chapters = db.query(Chapter).filter(Chapter.book_id == book_id).all()
    qa_records = {
        record.chapter_n: record
        for record in db.query(ChapterQARecord).filter(ChapterQARecord.book_id == book_id).all()
    }
    return _validate_export_chapters(
        chapters,
        include_only_approved=include_only_approved,
        qa_records=qa_records,
    )


def _queue_export_for_book(
    book: Book,
    request: ExportRequest | BatchExportRequest,
    *,
    db: Session,
    session_factory: sessionmaker[Session],
) -> tuple[ExportJob, list[str], int, datetime]:
    """Create or update one export job row and launch its background worker."""

    try:
        formats = _normalize_export_formats(request.formats)
        expected_completion_seconds = estimate_export_seconds(
            book.id,
            export_formats=formats,
            include_only_approved=request.include_only_approved,
            session_factory=session_factory,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    existing_job = db.query(ExportJob).filter(ExportJob.book_id == book.id).first()
    existing_job = _reconcile_export_job_if_needed(book, existing_job, db)
    if existing_job is not None and existing_job.export_status == BookExportStatus.PROCESSING:
        raise HTTPException(status_code=409, detail="An export is already in progress for this book.")

    started_at = utc_now()
    job_token = f"export_{book.id}_{started_at.strftime('%Y%m%d_%H%M%S')}"
    pending_details = json.dumps(
        {
            name: result.model_dump(mode="json")
            for name, result in _empty_format_details(formats).items()
        }
    )

    if existing_job is None:
        export_job = ExportJob(
            book_id=book.id,
            job_token=job_token,
            export_status=BookExportStatus.PROCESSING,
            formats_requested=json.dumps(formats),
            format_details=pending_details,
            progress_percent=0.0,
            current_stage="Queued",
            current_format=None,
            current_chapter_n=None,
            total_chapters=None,
            include_only_approved=request.include_only_approved,
            created_at=started_at,
            started_at=started_at,
            completed_at=None,
            updated_at=started_at,
            error_message=None,
            qa_report=None,
        )
        db.add(export_job)
    else:
        export_job = existing_job
        export_job.job_token = job_token
        export_job.export_status = BookExportStatus.PROCESSING
        export_job.formats_requested = json.dumps(formats)
        export_job.format_details = pending_details
        export_job.progress_percent = 0.0
        export_job.current_stage = "Queued"
        export_job.current_format = None
        export_job.current_chapter_n = None
        export_job.total_chapters = None
        export_job.include_only_approved = request.include_only_approved
        export_job.created_at = started_at
        export_job.started_at = started_at
        export_job.completed_at = None
        export_job.updated_at = started_at
        export_job.error_message = None
        export_job.qa_report = None

    book.export_status = BookExportStatus.PROCESSING
    db.commit()
    db.refresh(export_job)

    _launch_export_job(export_job.id, session_factory=session_factory)
    invalidate_library_cache()
    return export_job, formats, expected_completion_seconds, started_at


def _launch_export_job(
    export_job_id: int,
    *,
    session_factory: sessionmaker[Session] | None = None,
) -> None:
    """Run one export job on the shared background executor."""

    future = _ensure_export_executor().submit(
        run_export_job_sync,
        export_job_id,
        session_factory=session_factory,
    )
    with _export_threads_lock:
        _export_futures[export_job_id] = future
    future.add_done_callback(lambda completed_future, job_id=export_job_id: _discard_export_future(job_id, completed_future))


def _book_is_ready_for_batch_export(db: Session, book: Book, *, include_only_approved: bool) -> bool:
    """Return whether the book is ready for batch export."""

    qa_records = {
        record.chapter_n: record
        for record in db.query(ChapterQARecord).filter(ChapterQARecord.book_id == book.id).all()
    }
    ready, _ = _validate_export_chapters(
        list(book.chapters),
        include_only_approved=include_only_approved,
        qa_records=qa_records,
    )
    return ready


def _mark_batch_export_book(
    *,
    state: BatchExportProgressResponse,
    book_id: int,
    title: str,
    status: str,
    error_message: str | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
) -> None:
    """Upsert one book row in the batch export progress state."""

    current = next((item for item in state.books if item.book_id == book_id), None)
    if current is None:
        state.books.append(
            BatchExportBookStatus(
                book_id=book_id,
                title=title,
                status=status,
                error_message=error_message,
                started_at=started_at,
                completed_at=completed_at,
            )
        )
        return

    current.status = status
    current.error_message = error_message
    current.started_at = started_at
    current.completed_at = completed_at


async def _monitor_batch_export(
    *,
    batch_id: str,
    book_ids: list[int],
    session_factory: sessionmaker[Session],
) -> None:
    """Poll queued export jobs until the batch reaches a terminal state."""

    global _batch_export_monitor_task

    try:
        while True:
            with _batch_export_lock:
                state = _batch_export_progress
                if state is None or state.batch_id != batch_id:
                    return

            with session_factory() as db_session:
                book_rows = (
                    db_session.query(Book)
                    .filter(Book.id.in_(book_ids))
                    .all()
                )
                books = {book.id: book.title for book in book_rows}
                export_jobs = {
                    job.book_id: job
                    for job in (
                        db_session.query(ExportJob)
                        .filter(ExportJob.book_id.in_(book_ids))
                        .all()
                    )
                }
                for book in book_rows:
                    export_job = export_jobs.get(book.id)
                    reconciled_job = _reconcile_export_job_if_needed(book, export_job, db_session)
                    if reconciled_job is not None:
                        export_jobs[book.id] = reconciled_job

            completed = 0
            failed = 0
            in_progress = 0

            with _batch_export_lock:
                state = _batch_export_progress
                if state is None or state.batch_id != batch_id:
                    return

                for book_id in book_ids:
                    book_title = books.get(book_id, f"Book {book_id}")
                    export_job = export_jobs.get(book_id)
                    if export_job is None:
                        _mark_batch_export_book(
                            state=state,
                            book_id=book_id,
                            title=book_title,
                            status="queued",
                        )
                        in_progress += 1
                        continue

                    status = export_job.export_status.value
                    if export_job.export_status == BookExportStatus.PROCESSING:
                        in_progress += 1
                    elif export_job.export_status == BookExportStatus.COMPLETED:
                        completed += 1
                    else:
                        failed += 1

                    _mark_batch_export_book(
                        state=state,
                        book_id=book_id,
                        title=book_title,
                        status=status,
                        error_message=export_job.error_message,
                        started_at=export_job.started_at,
                        completed_at=export_job.completed_at,
                    )

                state.completed = completed
                state.failed = failed
                state.in_progress = in_progress
                if in_progress == 0:
                    state.status = "completed"
                    state.completed_at = utc_now()
                    return
                state.status = "running"

            await asyncio.sleep(1.0)
    finally:
        _batch_export_monitor_task = None


@router.post("/book/{book_id}/export", response_model=ExportQueuedResponse)
async def export_book_endpoint(
    book_id: int,
    request: ExportRequest,
    db: Session = Depends(get_db),
) -> ExportQueuedResponse:
    """Trigger an asynchronous export job for the requested book."""

    book = _load_book_or_404(book_id, db)
    ready, reason = _validate_book_export_readiness(
        book_id,
        db,
        include_only_approved=request.include_only_approved,
    )
    if not ready:
        raise HTTPException(status_code=400, detail=reason)
    session_factory = _session_factory_for(db)
    export_job, formats, expected_completion_seconds, started_at = _queue_export_for_book(
        book,
        request,
        db=db,
        session_factory=session_factory,
    )

    return ExportQueuedResponse(
        book_id=book_id,
        export_status=BookExportStatus.PROCESSING.value,
        job_id=export_job.job_token,
        formats_requested=formats,
        expected_completion_seconds=expected_completion_seconds,
        started_at=started_at,
    )


@router.get("/book/{book_id}/export/status", response_model=ExportStatusResponse)
async def get_export_status(book_id: int, db: Session = Depends(get_db)) -> ExportStatusResponse:
    """Return the current or most recent export status for one book."""

    book = _load_book_or_404(book_id, db)
    export_job = db.query(ExportJob).filter(ExportJob.book_id == book_id).first()
    export_job = _reconcile_export_job_if_needed(book, export_job, db)
    return _serialize_status(book, export_job)


@router.post("/book/{book_id}/export/cancel", response_model=ExportCancelResponse)
async def cancel_export(book_id: int, db: Session = Depends(get_db)) -> ExportCancelResponse:
    """Force-cancel one in-flight export so a new export can be queued."""

    book = _load_book_or_404(book_id, db)
    export_job = db.query(ExportJob).filter(ExportJob.book_id == book_id).first()
    export_job = _reconcile_export_job_if_needed(book, export_job, db)
    if export_job is None or export_job.export_status != BookExportStatus.PROCESSING:
        raise HTTPException(status_code=400, detail="No export is currently in progress for this book.")

    cancelled_at = utc_now()
    export_job.export_status = BookExportStatus.ERROR
    export_job.completed_at = cancelled_at
    export_job.updated_at = cancelled_at
    export_job.current_stage = "Export cancelled"
    export_job.error_message = "Export cancelled by operator."
    book.export_status = BookExportStatus.ERROR
    db.commit()
    invalidate_library_cache()
    return ExportCancelResponse(
        book_id=book.id,
        export_status=BookExportStatus.ERROR.value,
        message="Export cancelled",
    )


def _queue_reexport_for_format(
    *,
    book: Book,
    export_format: str,
    export_job: ExportJob | None,
    db: Session,
) -> None:
    """Queue a replacement export after checksum verification rejects an existing file."""

    include_only_approved = export_job.include_only_approved if export_job is not None else True
    request = ExportRequest(formats=[export_format], include_only_approved=include_only_approved)
    _queue_export_for_book(
        book,
        request,
        db=db,
        session_factory=_session_factory_for(db),
    )
    invalidate_library_cache()


@router.get("/book/{book_id}/export/download/{export_format}")
async def download_export(book_id: int, export_format: str, db: Session = Depends(get_db)) -> FileResponse:
    """Serve a completed MP3 or M4B export file."""

    book = _load_book_or_404(book_id, db)

    normalized_format = export_format.strip().lower()
    if normalized_format not in {"mp3", "m4b"}:
        raise HTTPException(status_code=400, detail="Invalid format")

    export_job = db.query(ExportJob).filter(ExportJob.book_id == book_id).first()
    output_path = get_export_output_path(book, normalized_format)
    if not output_path.exists():
        raise HTTPException(status_code=404, detail="Export file not found")

    expected_sha256 = get_expected_export_sha256(
        book,
        normalized_format,
        stored_format_details=export_job.format_details if export_job is not None else None,
    )
    if expected_sha256 and not _verify_checksum(output_path, expected_sha256):
        output_path.unlink(missing_ok=True)
        try:
            _queue_reexport_for_format(
                book=book,
                export_format=normalized_format,
                export_job=export_job,
                db=db,
            )
        except HTTPException as exc:
            logger.warning(
                "Failed to queue replacement export for corrupted %s book %s download: %s",
                normalized_format,
                book.id,
                exc.detail,
            )
            raise HTTPException(
                status_code=409,
                detail="Export file failed checksum verification and was deleted. Re-export could not be queued.",
            ) from exc
        raise HTTPException(
            status_code=409,
            detail="Export file failed checksum verification and was deleted. Re-export queued.",
        )

    media_type = "audio/mpeg" if normalized_format == "mp3" else "audio/mp4"
    return FileResponse(
        path=output_path,
        media_type=media_type,
        filename=output_path.name,
    )


@router.post("/export/batch", response_model=BatchExportQueuedResponse)
async def batch_export(
    request: BatchExportRequest,
    db: Session = Depends(get_db),
) -> BatchExportQueuedResponse:
    """Queue exports for all ready books and start progress tracking."""

    global _batch_export_progress, _batch_export_monitor_task

    with _batch_export_lock:
        if _batch_export_monitor_task is not None and not _batch_export_monitor_task.done():
            raise HTTPException(status_code=409, detail="A batch export is already running.")

    session_factory = _session_factory_for(db)
    started_at = utc_now()
    batch_id = f"batch_export_{started_at.strftime('%Y%m%d_%H%M%S')}"
    try:
        formats = _normalize_export_formats(request.formats)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    books = (
        db.query(Book)
        .options(selectinload(Book.chapters))
        .order_by(Book.id.asc())
        .all()
    )

    state = BatchExportProgressResponse(
        batch_id=batch_id,
        status="queued",
        total_books=0,
        queued=0,
        completed=0,
        failed=0,
        skipped=0,
        not_ready=0,
        in_progress=0,
        formats_requested=formats,
        include_only_approved=request.include_only_approved,
        started_at=started_at,
        books=[],
    )
    queued_book_ids: list[int] = []

    for book in books:
        if request.skip_already_exported and book.export_status == BookExportStatus.COMPLETED:
            state.skipped += 1
            _mark_batch_export_book(
                state=state,
                book_id=book.id,
                title=book.title,
                status="skipped",
            )
            continue

        existing_job = db.query(ExportJob).filter(ExportJob.book_id == book.id).first()
        if existing_job is not None and existing_job.export_status == BookExportStatus.PROCESSING:
            state.skipped += 1
            _mark_batch_export_book(
                state=state,
                book_id=book.id,
                title=book.title,
                status="processing",
            )
            continue

        if not _book_is_ready_for_batch_export(db, book, include_only_approved=request.include_only_approved):
            state.not_ready += 1
            _mark_batch_export_book(
                state=state,
                book_id=book.id,
                title=book.title,
                status="not_ready",
            )
            continue

        try:
            export_job, _, _, _ = _queue_export_for_book(
                book,
                request,
                db=db,
                session_factory=session_factory,
            )
        except HTTPException as exc:
            if exc.status_code == 409:
                state.skipped += 1
                _mark_batch_export_book(
                    state=state,
                    book_id=book.id,
                    title=book.title,
                    status="processing",
                    error_message=exc.detail,
                )
                continue
            raise

        state.queued += 1
        queued_book_ids.append(book.id)
        _mark_batch_export_book(
            state=state,
            book_id=book.id,
            title=book.title,
            status=export_job.export_status.value,
            started_at=export_job.started_at,
        )

    state.total_books = state.queued + state.skipped + state.not_ready
    state.in_progress = state.queued
    state.status = "completed" if state.queued == 0 else "running"
    if state.queued == 0:
        state.completed_at = utc_now()

    with _batch_export_lock:
        _batch_export_progress = state
        if queued_book_ids:
            _batch_export_monitor_task = _track_export_task(asyncio.create_task(
                _monitor_batch_export(
                    batch_id=batch_id,
                    book_ids=queued_book_ids,
                    session_factory=session_factory,
                ),
                name=f"batch-export-{batch_id}",
            ))
            _batch_export_monitor_task.add_done_callback(_discard_export_task)
        else:
            _batch_export_monitor_task = None

    return BatchExportQueuedResponse(
        batch_id=batch_id,
        status=state.status,
        queued=state.queued,
        skipped=state.skipped,
        not_ready=state.not_ready,
        started_at=started_at,
    )


@router.get("/export/batch/progress", response_model=BatchExportProgressResponse | None)
async def get_batch_export_progress() -> BatchExportProgressResponse | None:
    """Return the active or most recent batch export progress payload."""

    with _batch_export_lock:
        return _batch_export_progress
