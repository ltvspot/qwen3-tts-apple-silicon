"""Export API routes for audiobook packaging and downloads."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker

from src.api.cache import invalidate_library_cache
from src.database import Book, BookExportStatus, ExportJob, get_db, utc_now
from src.pipeline.exporter import (
    ExportFormatResult,
    QAReport,
    _empty_format_details,
    _normalize_export_formats,
    estimate_export_seconds,
    get_export_output_path,
    run_export_job,
)

router = APIRouter(prefix="/api", tags=["export"])

_export_tasks: set[asyncio.Task[None]] = set()


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
    started_at: datetime | None = None
    completed_at: datetime | None = None


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
        started_at=export_job.started_at,
        completed_at=export_job.completed_at,
    )


def _load_book_or_404(book_id: int, db: Session) -> Book:
    """Load a book or raise a 404."""

    book = db.query(Book).filter(Book.id == book_id).first()
    if book is None:
        raise HTTPException(status_code=404, detail=f"Book {book_id} not found")
    return book


def _launch_export_job(
    export_job_id: int,
    *,
    session_factory: sessionmaker[Session] | None = None,
) -> None:
    """Schedule export execution in the background and retain the task reference."""

    task = asyncio.create_task(run_export_job(export_job_id, session_factory=session_factory))
    _export_tasks.add(task)
    task.add_done_callback(_export_tasks.discard)


@router.post("/book/{book_id}/export", response_model=ExportQueuedResponse)
async def export_book_endpoint(
    book_id: int,
    request: ExportRequest,
    db: Session = Depends(get_db),
) -> ExportQueuedResponse:
    """Trigger an asynchronous export job for the requested book."""

    book = _load_book_or_404(book_id, db)
    session_factory = _session_factory_for(db)

    try:
        formats = _normalize_export_formats(request.formats)
        expected_completion_seconds = estimate_export_seconds(
            book_id,
            export_formats=formats,
            include_only_approved=request.include_only_approved,
            session_factory=session_factory,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    existing_job = db.query(ExportJob).filter(ExportJob.book_id == book_id).first()
    if existing_job is not None and existing_job.export_status == BookExportStatus.PROCESSING:
        raise HTTPException(status_code=409, detail="An export is already in progress for this book.")

    started_at = utc_now()
    job_token = f"export_{book_id}_{started_at.strftime('%Y%m%d_%H%M%S')}"
    pending_details = json.dumps(
        {
            name: result.model_dump(mode="json")
            for name, result in _empty_format_details(formats).items()
        }
    )

    if existing_job is None:
        export_job = ExportJob(
            book_id=book_id,
            job_token=job_token,
            export_status=BookExportStatus.PROCESSING,
            formats_requested=json.dumps(formats),
            format_details=pending_details,
            include_only_approved=request.include_only_approved,
            created_at=started_at,
            started_at=started_at,
            completed_at=None,
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
        export_job.include_only_approved = request.include_only_approved
        export_job.created_at = started_at
        export_job.started_at = started_at
        export_job.completed_at = None
        export_job.error_message = None
        export_job.qa_report = None

    book.export_status = BookExportStatus.PROCESSING
    db.commit()
    db.refresh(export_job)

    _launch_export_job(export_job.id, session_factory=session_factory)
    invalidate_library_cache()

    return ExportQueuedResponse(
        book_id=book_id,
        export_status=BookExportStatus.PROCESSING.value,
        job_id=job_token,
        formats_requested=formats,
        expected_completion_seconds=expected_completion_seconds,
        started_at=started_at,
    )


@router.get("/book/{book_id}/export/status", response_model=ExportStatusResponse)
async def get_export_status(book_id: int, db: Session = Depends(get_db)) -> ExportStatusResponse:
    """Return the current or most recent export status for one book."""

    book = _load_book_or_404(book_id, db)
    export_job = db.query(ExportJob).filter(ExportJob.book_id == book_id).first()
    return _serialize_status(book, export_job)


@router.get("/book/{book_id}/export/download/{export_format}")
async def download_export(book_id: int, export_format: str, db: Session = Depends(get_db)) -> FileResponse:
    """Serve a completed MP3 or M4B export file."""

    book = _load_book_or_404(book_id, db)

    normalized_format = export_format.strip().lower()
    if normalized_format not in {"mp3", "m4b"}:
        raise HTTPException(status_code=400, detail="Invalid format")

    output_path = get_export_output_path(book, normalized_format)
    if not output_path.exists():
        raise HTTPException(status_code=404, detail="Export file not found")

    media_type = "audio/mpeg" if normalized_format == "mp3" else "audio/mp4"
    return FileResponse(
        path=output_path,
        media_type=media_type,
        filename=output_path.name,
    )
