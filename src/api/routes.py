"""FastAPI routes for library indexing and manuscript parsing."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session, selectinload

from src.api.cache import invalidate_library_cache, library_cache
from src.api.library import LibraryScanner
from src.config import get_application_settings, settings
from src.database import (
    AudioQAResult,
    Book,
    BookExportStatus,
    BookGenerationStatus,
    BookStatus,
    Chapter,
    ChapterQARecord,
    ChapterStatus,
    ChapterType,
    ExportJob,
    QAStatus,
    get_db,
    utc_now,
)
from src.pipeline.book_qa import build_export_readiness_summary
from src.pipeline.manuscript_validator import ManuscriptValidationReport, ManuscriptValidator
from src.pipeline.pronunciation_watchlist import PronunciationWatchlist
from src.parser import Chapter as ParsedManuscriptChapter
from src.parser import CreditsGenerator, ManuscriptParserFactory
from src.parser.common import AUTO_SPLIT_ESTIMATED_MINUTES, estimate_duration_minutes, split_text_at_paragraph

logger = logging.getLogger(__name__)


class BookResponse(BaseModel):
    """Response model for a book."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    subtitle: str | None
    description: str | None
    author: str
    narrator: str
    folder_path: str
    status: BookStatus | None
    page_count: int | None
    trim_size: str | None
    chapter_count: int
    created_at: datetime
    updated_at: datetime
    generation_status: BookGenerationStatus
    generation_started_at: datetime | None
    generation_eta_seconds: int | None


class ChapterResponse(BaseModel):
    """Response model for a chapter."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    book_id: int
    number: int
    title: str | None
    type: ChapterType
    text_content: str | None
    word_count: int | None
    status: ChapterStatus
    audio_path: str | None
    duration_seconds: float | None
    qa_status: QAStatus | None
    qa_notes: str | None
    started_at: datetime | None
    completed_at: datetime | None
    error_message: str | None
    audio_file_size_bytes: int | None
    created_at: datetime
    updated_at: datetime


class LibraryScanResponse(BaseModel):
    """Response model for a library scan."""

    total_found: int
    total_indexed: int
    new_books: int
    errors: list[str]


class ManuscriptIssueResponse(BaseModel):
    """One pre-generation manuscript issue."""

    severity: str
    chapter: int | None
    description: str
    suggestion: str


class ManuscriptIssueSummaryResponse(BaseModel):
    """Counts of manuscript issues by severity."""

    errors: int = 0
    warnings: int = 0
    info: int = 0


class ManuscriptValidationResponse(BaseModel):
    """Serialized manuscript validation payload."""

    book_id: int
    title: str
    total_chapters: int
    total_words: int
    difficulty_score: float
    ready_for_generation: bool
    issues: list[ManuscriptIssueResponse]
    issue_summary: ManuscriptIssueSummaryResponse


class LibraryScanProgressResponse(BaseModel):
    """Live scan progress payload for the library page."""

    scanning: bool
    files_found: int
    files_processed: int
    elapsed_seconds: int
    new_books: int = 0
    errors: list[str] = Field(default_factory=list)


class ParseBookRequest(BaseModel):
    """Request payload for parsing a book's manuscript."""

    overwrite: bool = False


class ChapterUpdateRequest(BaseModel):
    """Request payload for chapter text corrections."""

    text_content: str


class ParseBookResponse(BaseModel):
    """Response payload for parse-book operations."""

    status: str
    chapters_detected: int
    message: str


class SplitChapterRequest(BaseModel):
    """Request payload for manually splitting one parsed chapter."""

    split_point: str = "auto"
    paragraph_index: int | None = None

    @model_validator(mode="after")
    def _validate_request(self) -> "SplitChapterRequest":
        """Require either auto mode or one explicit paragraph index."""

        if self.paragraph_index is not None:
            return self
        if self.split_point != "auto":
            raise ValueError("split_point must be 'auto' when paragraph_index is not provided.")
        return self


class ExportReadinessCheckResponse(BaseModel):
    """One ACX/export checklist item for the frontend summary panel."""

    key: str
    label: str
    passed: bool
    detail: str
    critical: bool = True


class ExportReadinessChapterResponse(BaseModel):
    """Per-chapter export readiness summary row."""

    id: int
    number: int
    title: str | None
    duration_seconds: float | None
    grade: str
    issues: list[str] = Field(default_factory=list)


class ExportReadinessResponse(BaseModel):
    """Consolidated export-readiness payload for one book."""

    ready: bool
    export_anyway_allowed: bool
    status_label: str
    acx_checks: list[ExportReadinessCheckResponse]
    chapters: list[ExportReadinessChapterResponse]
    blocking_issues: list[str]
    warnings: list[str]


class LibraryStats(BaseModel):
    """Counts of books per lifecycle state."""

    not_started: int = 0
    parsed: int = 0
    generating: int = 0
    generated: int = 0
    qa: int = 0
    qa_approved: int = 0
    exported: int = 0


class LibraryResponse(BaseModel):
    """Response model for paginated library queries."""

    total: int
    books: list[BookResponse]
    stats: LibraryStats


class PronunciationWatchlistEntry(BaseModel):
    """One pronunciation watchlist entry."""

    word: str
    pronunciation_guide: str
    context: str


class PronunciationWatchlistResponse(BaseModel):
    """Serialized pronunciation watchlist payload."""

    entries: list[PronunciationWatchlistEntry]


class PronunciationWatchlistAddRequest(BaseModel):
    """Request payload for adding or updating a watchlist word."""

    word: str = Field(min_length=1, max_length=255)
    guide: str = Field(min_length=1, max_length=255)


class BookPronunciationWatchlistWord(BaseModel):
    """One per-book pronunciation override."""

    word: str = Field(min_length=1, max_length=255)
    phonetic: str = Field(min_length=1, max_length=255)


class BookPronunciationWatchlistRequest(BaseModel):
    """Request payload for replacing one book's pronunciation overrides."""

    words: list[BookPronunciationWatchlistWord] = Field(default_factory=list)


class BookPronunciationWatchlistResponse(BaseModel):
    """Serialized per-book pronunciation overrides."""

    book_id: int
    entries: list[BookPronunciationWatchlistWord]


router = APIRouter(prefix="/api", tags=["library"])
_library_scan_lock = threading.RLock()
_library_scan_progress: dict[str, object] = {
    "elapsed_seconds": 0,
    "errors": [],
    "files_found": 0,
    "files_processed": 0,
    "new_books": 0,
    "scanning": False,
    "started_monotonic": None,
}


def _title_with_part_suffix(title: str | None, *, part: int) -> str:
    """Return a stable split-chapter title with a part suffix."""

    base_title = (title or "Chapter").strip() or "Chapter"
    base_title = re.sub(r"\s+\(Part\s+\d+\)\s*$", "", base_title, flags=re.IGNORECASE)
    return f"{base_title} (Part {part})"


def _auto_split_parsed_chapters(chapters: list[ParsedManuscriptChapter]) -> list[ParsedManuscriptChapter]:
    """Split oversized parsed chapters on paragraph boundaries before DB persistence."""

    pending = list(chapters)
    normalized: list[ParsedManuscriptChapter] = []
    while pending:
        parsed_chapter = pending.pop(0)
        estimated_minutes = estimate_duration_minutes(parsed_chapter.word_count)
        if estimated_minutes <= AUTO_SPLIT_ESTIMATED_MINUTES:
            normalized.append(parsed_chapter)
            continue

        split_result = split_text_at_paragraph(parsed_chapter.raw_text)
        if split_result is None:
            logger.warning(
                "Chapter '%s' is estimated at %.1f minutes but could not be auto-split because no paragraph boundary was found.",
                parsed_chapter.title,
                estimated_minutes,
            )
            normalized.append(parsed_chapter)
            continue

        left_chapter = ParsedManuscriptChapter(
            number=parsed_chapter.number,
            title=_title_with_part_suffix(parsed_chapter.title, part=1),
            type=parsed_chapter.type,
            raw_text=split_result.left_text,
            word_count=split_result.left_word_count,
        )
        right_chapter = ParsedManuscriptChapter(
            number=parsed_chapter.number,
            title=_title_with_part_suffix(parsed_chapter.title, part=2),
            type=parsed_chapter.type,
            raw_text=split_result.right_text,
            word_count=split_result.right_word_count,
        )
        logger.info(
            "Auto-split parsed chapter '%s' (%.1f min estimate) into '%s' (%.1f min) and '%s' (%.1f min).",
            parsed_chapter.title,
            estimated_minutes,
            left_chapter.title,
            estimate_duration_minutes(left_chapter.word_count),
            right_chapter.title,
            estimate_duration_minutes(right_chapter.word_count),
        )
        pending.insert(0, right_chapter)
        pending.insert(0, left_chapter)

    return normalized


def _shift_numbered_rows(records: list[object], attribute_name: str, *, delta: int, db: Session) -> None:
    """Shift rows that carry unique chapter-number keys without colliding in-place."""

    if not records or delta == 0:
        return

    temporary_offset = 100_000
    for record in records:
        current_value = getattr(record, attribute_name)
        setattr(record, attribute_name, current_value + temporary_offset)
    db.flush()
    for record in records:
        current_value = getattr(record, attribute_name)
        setattr(record, attribute_name, current_value - temporary_offset + delta)
    db.flush()


def _retag_shifted_qa_payloads(records: list[ChapterQARecord]) -> None:
    """Keep embedded chapter-number metadata aligned after renumbering QA rows."""

    for record in records:
        try:
            payload = json.loads(record.qa_details)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        payload["chapter_n"] = record.chapter_n
        chapter_report = payload.get("chapter_report")
        if isinstance(chapter_report, dict):
            chapter_report["chapter_number"] = record.chapter_n
            payload["chapter_report"] = chapter_report
        record.qa_details = json.dumps(payload)


def _retag_shifted_audio_qa_payloads(records: list[AudioQAResult]) -> None:
    """Keep embedded chapter-number metadata aligned after renumbering deep-QA rows."""

    for record in records:
        try:
            payload = json.loads(record.report_json)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        payload["chapter_n"] = record.chapter_n
        record.report_json = json.dumps(payload)


def _invalidate_chapter_artifacts(chapter: Chapter) -> None:
    """Reset generation and QA artifacts for a chapter whose text changed."""

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


def _split_persisted_chapter(
    *,
    book: Book,
    chapter: Chapter,
    paragraph_index: int | None,
    db: Session,
) -> tuple[Chapter, Chapter]:
    """Split one persisted chapter and renumber following chapters and QA rows."""

    if chapter.type in {ChapterType.OPENING_CREDITS, ChapterType.CLOSING_CREDITS}:
        raise HTTPException(status_code=400, detail="Opening and closing credits cannot be split.")
    if book.generation_status == BookGenerationStatus.GENERATING:
        raise HTTPException(status_code=409, detail="Stop generation before splitting chapters.")
    if book.export_status == BookExportStatus.PROCESSING:
        raise HTTPException(status_code=409, detail="Wait for export to finish before splitting chapters.")

    split_result = split_text_at_paragraph(chapter.text_content or "", paragraph_index=paragraph_index)
    if split_result is None:
        raise HTTPException(
            status_code=400,
            detail="Chapter could not be split. It needs at least two non-empty paragraphs.",
        )

    chapters_to_shift = (
        db.query(Chapter)
        .filter(Chapter.book_id == book.id, Chapter.number > chapter.number)
        .order_by(Chapter.number.desc(), Chapter.id.desc())
        .all()
    )
    for shifted_chapter in chapters_to_shift:
        shifted_chapter.number += 1

    qa_records_to_shift = (
        db.query(ChapterQARecord)
        .filter(ChapterQARecord.book_id == book.id, ChapterQARecord.chapter_n > chapter.number)
        .order_by(ChapterQARecord.chapter_n.desc(), ChapterQARecord.id.desc())
        .all()
    )
    _shift_numbered_rows(qa_records_to_shift, "chapter_n", delta=1, db=db)
    _retag_shifted_qa_payloads(qa_records_to_shift)

    audio_qa_to_shift = (
        db.query(AudioQAResult)
        .filter(AudioQAResult.book_id == book.id, AudioQAResult.chapter_n > chapter.number)
        .order_by(AudioQAResult.chapter_n.desc(), AudioQAResult.id.desc())
        .all()
    )
    _shift_numbered_rows(audio_qa_to_shift, "chapter_n", delta=1, db=db)
    _retag_shifted_audio_qa_payloads(audio_qa_to_shift)

    db.query(ChapterQARecord).filter(
        ChapterQARecord.book_id == book.id,
        ChapterQARecord.chapter_n == chapter.number,
    ).delete(synchronize_session=False)
    db.query(AudioQAResult).filter(
        AudioQAResult.book_id == book.id,
        AudioQAResult.chapter_n == chapter.number,
    ).delete(synchronize_session=False)

    chapter.title = _title_with_part_suffix(chapter.title, part=1)
    chapter.text_content = split_result.left_text
    chapter.word_count = split_result.left_word_count
    _invalidate_chapter_artifacts(chapter)

    split_chapter = Chapter(
        book_id=book.id,
        number=chapter.number + 1,
        title=_title_with_part_suffix(chapter.title, part=2),
        type=chapter.type,
        text_content=split_result.right_text,
        word_count=split_result.right_word_count,
        status=ChapterStatus.PENDING,
        qa_status=QAStatus.NOT_REVIEWED,
    )
    db.add(split_chapter)

    book.status = BookStatus.PARSED
    book.generation_status = BookGenerationStatus.IDLE
    book.export_status = BookExportStatus.IDLE
    export_job = db.query(ExportJob).filter(ExportJob.book_id == book.id).first()
    if export_job is not None:
        export_job.export_status = BookExportStatus.ERROR
        export_job.completed_at = utc_now()
        export_job.updated_at = export_job.completed_at
        export_job.current_stage = "Chapter split requires re-export"
        export_job.error_message = "Chapter structure changed after a manual split. Re-export required."

    db.flush()
    logger.info(
        "Manually split book %s chapter %s into chapter ids %s and %s at paragraph index %s.",
        book.id,
        chapter.number,
        chapter.id,
        split_chapter.id,
        split_result.paragraph_index,
    )
    return chapter, split_chapter


def _set_library_scan_progress(**updates: object) -> None:
    """Mutate the shared scan progress state under the module lock."""

    with _library_scan_lock:
        _library_scan_progress.update(updates)


def _snapshot_library_scan_progress() -> LibraryScanProgressResponse:
    """Return the current scan progress payload."""

    with _library_scan_lock:
        started_monotonic = _library_scan_progress.get("started_monotonic")
        elapsed_seconds = int(
            max(time.monotonic() - started_monotonic, 0)
        ) if isinstance(started_monotonic, (int, float)) else int(_library_scan_progress.get("elapsed_seconds", 0))
        return LibraryScanProgressResponse(
            scanning=bool(_library_scan_progress.get("scanning", False)),
            files_found=int(_library_scan_progress.get("files_found", 0)),
            files_processed=int(_library_scan_progress.get("files_processed", 0)),
            elapsed_seconds=elapsed_seconds,
            new_books=int(_library_scan_progress.get("new_books", 0)),
            errors=list(_library_scan_progress.get("errors", [])),
        )


def _serialize_book(book: Book) -> BookResponse:
    """Convert a Book ORM instance into an API response model."""

    return BookResponse(
        id=book.id,
        title=book.title,
        subtitle=book.subtitle,
        description=book.description,
        author=book.author,
        narrator=book.narrator,
        folder_path=book.folder_path,
        status=book.status,
        page_count=book.page_count,
        trim_size=book.trim_size,
        chapter_count=len(book.chapters),
        created_at=book.created_at,
        updated_at=book.updated_at,
        generation_status=book.generation_status,
        generation_started_at=book.generation_started_at,
        generation_eta_seconds=book.generation_eta_seconds,
    )


def _serialize_chapter(chapter: Chapter) -> ChapterResponse:
    """Convert a Chapter ORM instance into an API response model."""

    return ChapterResponse.model_validate(chapter)


def _serialize_manuscript_report(report: ManuscriptValidationReport) -> ManuscriptValidationResponse:
    """Convert manuscript validation dataclasses into API payloads."""

    issue_summary = ManuscriptIssueSummaryResponse()
    for issue in report.issues:
        if issue.severity == "error":
            issue_summary.errors += 1
        elif issue.severity == "warning":
            issue_summary.warnings += 1
        else:
            issue_summary.info += 1

    return ManuscriptValidationResponse(
        book_id=report.book_id,
        title=report.title,
        total_chapters=report.total_chapters,
        total_words=report.total_words,
        difficulty_score=report.difficulty_score,
        ready_for_generation=report.ready_for_generation,
        issues=[
            ManuscriptIssueResponse(
                severity=issue.severity,
                chapter=issue.chapter,
                description=issue.description,
                suggestion=issue.suggestion,
            )
            for issue in report.issues
        ],
        issue_summary=issue_summary,
    )


def _pronunciation_watchlist() -> PronunciationWatchlist:
    """Return the pronunciation watchlist helper."""

    return PronunciationWatchlist()


def _build_manuscript_validation(book: Book, chapters: list[Chapter]) -> ManuscriptValidationResponse:
    """Run manuscript validation for a loaded book."""

    report = ManuscriptValidator.validate(
        book.id,
        [
            {
                "book_title": book.title,
                "number": chapter.number,
                "text": chapter.text_content or "",
            }
            for chapter in chapters
        ],
    )
    return _serialize_manuscript_report(report)


def _build_library_stats(db: Session) -> LibraryStats:
    """Return aggregate counts for the whole indexed library."""

    counts = {status.value: 0 for status in BookStatus}
    for row in db.query(Book.status).all():
        if row[0] is not None:
            counts[row[0].value if isinstance(row[0], BookStatus) else str(row[0])] += 1
    return LibraryStats(**counts)


def _library_cache_key(*, status_filter: BookStatus | None, limit: int, offset: int, sort: str) -> str:
    """Build the cache key for a library listing query."""

    return f"library:{status_filter.value if status_filter is not None else 'all'}:{sort}:{limit}:{offset}"


def _apply_library_sort(query, sort: str):
    """Apply a stable ordering for library list queries."""

    if sort == "updated_at":
        return query.order_by(Book.updated_at.desc(), Book.id.desc())
    if sort == "title":
        return query.order_by(Book.title.asc(), Book.id.asc())
    if sort == "author":
        return query.order_by(Book.author.asc(), Book.id.asc())
    if sort != "created_at":
        raise HTTPException(status_code=400, detail="Unsupported library sort.")
    return query.order_by(Book.created_at.asc(), Book.id.asc())


@router.post("/library/scan", response_model=LibraryScanResponse)
def scan_library(db: Session = Depends(get_db)) -> LibraryScanResponse:
    """Scan the manuscript root and index any newly discovered book folders."""

    with _library_scan_lock:
        if bool(_library_scan_progress.get("scanning", False)):
            raise HTTPException(status_code=409, detail="A library scan is already in progress.")
        _library_scan_progress.update(
            {
                "elapsed_seconds": 0,
                "errors": [],
                "files_found": 0,
                "files_processed": 0,
                "new_books": 0,
                "scanning": True,
                "started_monotonic": time.monotonic(),
            }
        )

    scanner = LibraryScanner()
    try:
        result = scanner.scan(
            db,
            progress_callback=lambda progress: _set_library_scan_progress(**progress),
        )
        db.commit()
        elapsed_seconds = _snapshot_library_scan_progress().elapsed_seconds
        _set_library_scan_progress(
            scanning=False,
            started_monotonic=None,
            elapsed_seconds=elapsed_seconds,
            files_found=result["total_found"],
            files_processed=result["total_found"],
            new_books=result["new_books"],
            errors=list(result["errors"]),
        )
        logger.info(
            "Library scan complete: found=%s indexed=%s new=%s errors=%s",
            result["total_found"],
            result["total_indexed"],
            result["new_books"],
            len(result["errors"]),
        )
        invalidate_library_cache()
        return LibraryScanResponse(**result)
    except Exception:
        db.rollback()
        elapsed_seconds = _snapshot_library_scan_progress().elapsed_seconds
        _set_library_scan_progress(
            scanning=False,
            started_monotonic=None,
            elapsed_seconds=elapsed_seconds,
        )
        raise


@router.get("/library/scan/progress", response_model=LibraryScanProgressResponse)
def get_library_scan_progress() -> LibraryScanProgressResponse:
    """Return the current library scan progress snapshot."""

    return _snapshot_library_scan_progress()


@router.get("/pronunciation-watchlist", response_model=PronunciationWatchlistResponse)
def get_pronunciation_watchlist() -> PronunciationWatchlistResponse:
    """Return the current pronunciation watchlist."""

    return PronunciationWatchlistResponse(entries=_pronunciation_watchlist().entries())


@router.post("/pronunciation-watchlist", response_model=PronunciationWatchlistResponse)
def add_pronunciation_watchlist_word(
    request: PronunciationWatchlistAddRequest,
) -> PronunciationWatchlistResponse:
    """Add or update a pronunciation watchlist entry."""

    watchlist = _pronunciation_watchlist()
    watchlist.add_word(request.word.strip(), request.guide.strip())
    return PronunciationWatchlistResponse(entries=watchlist.entries())


@router.delete("/pronunciation-watchlist/{word}", response_model=PronunciationWatchlistResponse)
def delete_pronunciation_watchlist_word(word: str) -> PronunciationWatchlistResponse:
    """Remove a pronunciation watchlist entry when present."""

    watchlist = _pronunciation_watchlist()
    watchlist.remove_word(word)
    return PronunciationWatchlistResponse(entries=watchlist.entries())


@router.put("/book/{book_id}/watchlist", response_model=BookPronunciationWatchlistResponse)
def update_book_pronunciation_watchlist(
    book_id: int,
    request: BookPronunciationWatchlistRequest,
    db: Session = Depends(get_db),
) -> BookPronunciationWatchlistResponse:
    """Replace the per-book pronunciation override list used during generation."""

    book = db.query(Book).filter(Book.id == book_id).first()
    if book is None:
        raise HTTPException(status_code=404, detail=f"Book {book_id} not found")

    watchlist = _pronunciation_watchlist()
    payload = [
        {
            "word": entry.word.strip(),
            "phonetic": entry.phonetic.strip(),
        }
        for entry in request.words
    ]
    book.pronunciation_watchlist = watchlist.serialize_custom_entries(payload) if payload else None
    db.commit()
    return BookPronunciationWatchlistResponse(
        book_id=book.id,
        entries=[BookPronunciationWatchlistWord(**entry) for entry in watchlist.custom_entries_from_payload(book.pronunciation_watchlist)],
    )


@router.get("/library", response_model=LibraryResponse)
async def get_library(
    status_filter: BookStatus | None = None,
    sort: str = Query(default="created_at"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> LibraryResponse:
    """Return paginated library books plus lifecycle statistics."""

    cache_key = _library_cache_key(status_filter=status_filter, limit=limit, offset=offset, sort=sort)
    cached_payload = library_cache.get(cache_key)
    if cached_payload is not None:
        return LibraryResponse(**cached_payload)

    query = db.query(Book).options(selectinload(Book.chapters))
    if status_filter is not None:
        query = query.filter(Book.status == status_filter)
    query = _apply_library_sort(query, sort)

    total = query.count()
    books = query.offset(offset).limit(limit).all()

    response = LibraryResponse(
        total=total,
        books=[_serialize_book(book) for book in books],
        stats=_build_library_stats(db),
    )
    library_cache.set(cache_key, response.model_dump(mode="json"))
    return response


@router.get("/book/{book_id}", response_model=BookResponse)
async def get_book(book_id: int, db: Session = Depends(get_db)) -> BookResponse:
    """Return a single indexed book."""

    book = (
        db.query(Book)
        .options(selectinload(Book.chapters))
        .filter(Book.id == book_id)
        .first()
    )
    if book is None:
        raise HTTPException(status_code=404, detail=f"Book {book_id} not found")
    return _serialize_book(book)


class BookMetadataUpdate(BaseModel):
    """Request body for updating book metadata."""

    subtitle: str | None = None
    description: str | None = None


class BulkBookMetadataItem(BaseModel):
    """One item in a bulk metadata update."""

    book_id: int
    subtitle: str | None = None
    description: str | None = None


class BulkBookMetadataResponse(BaseModel):
    """Response from a bulk metadata update."""

    updated: int
    skipped: int
    errors: list[str]


@router.post("/books/bulk-metadata", response_model=BulkBookMetadataResponse)
async def bulk_update_book_metadata(
    items: list[BulkBookMetadataItem],
    db: Session = Depends(get_db),
) -> BulkBookMetadataResponse:
    """Bulk update subtitles and/or descriptions for multiple books."""

    updated = 0
    skipped = 0
    errors: list[str] = []
    for item in items:
        book = db.query(Book).filter(Book.id == item.book_id).first()
        if book is None:
            errors.append(f"Book {item.book_id} not found")
            continue
        changed = False
        if item.subtitle is not None:
            book.subtitle = item.subtitle
            changed = True
        if item.description is not None:
            book.description = item.description
            changed = True
        if changed:
            updated += 1
        else:
            skipped += 1
    db.commit()
    return BulkBookMetadataResponse(updated=updated, skipped=skipped, errors=errors)


@router.patch("/book/{book_id}", response_model=BookResponse)
async def update_book_metadata(
    book_id: int,
    payload: BookMetadataUpdate,
    db: Session = Depends(get_db),
) -> BookResponse:
    """Update a book's subtitle and/or description."""

    book = (
        db.query(Book)
        .options(selectinload(Book.chapters))
        .filter(Book.id == book_id)
        .first()
    )
    if book is None:
        raise HTTPException(status_code=404, detail=f"Book {book_id} not found")
    if payload.subtitle is not None:
        book.subtitle = payload.subtitle
    if payload.description is not None:
        book.description = payload.description
    db.commit()
    db.refresh(book)
    return _serialize_book(book)


@router.get("/book/{book_id}/validate-manuscript", response_model=ManuscriptValidationResponse)
async def validate_manuscript(book_id: int, db: Session = Depends(get_db)) -> ManuscriptValidationResponse:
    """Return the pre-generation manuscript validation report for one book."""

    book = (
        db.query(Book)
        .options(selectinload(Book.chapters))
        .filter(Book.id == book_id)
        .first()
    )
    if book is None:
        raise HTTPException(status_code=404, detail=f"Book {book_id} not found")
    return _build_manuscript_validation(book, list(book.chapters))


@router.get("/book/{book_id}/chapters", response_model=list[ChapterResponse])
async def get_book_chapters(book_id: int, db: Session = Depends(get_db)) -> list[ChapterResponse]:
    """Return all stored chapters for a book in narration order."""

    book = db.query(Book.id).filter(Book.id == book_id).first()
    if book is None:
        raise HTTPException(status_code=404, detail=f"Book {book_id} not found")

    chapters = db.query(Chapter).filter(Chapter.book_id == book_id).order_by(Chapter.number, Chapter.id).all()
    return [_serialize_chapter(chapter) for chapter in chapters]


@router.get("/book/{book_id}/chapter/{chapter_number}/preview")
async def preview_chapter_audio(
    book_id: int,
    chapter_number: int,
    db: Session = Depends(get_db),
) -> FileResponse:
    """Stream generated chapter audio for the native in-browser preview player."""

    chapter = (
        db.query(Chapter)
        .filter(Chapter.book_id == book_id, Chapter.number == chapter_number)
        .first()
    )
    if chapter is None:
        raise HTTPException(status_code=404, detail=f"Chapter {chapter_number} not found in book {book_id}")
    if not chapter.audio_path:
        raise HTTPException(status_code=404, detail="Audio not yet generated for this chapter")

    outputs_root = Path(settings.OUTPUTS_PATH).resolve()
    audio_file = (outputs_root / chapter.audio_path).resolve()
    if outputs_root not in audio_file.parents:
        raise HTTPException(status_code=400, detail="Invalid audio path")
    if not audio_file.exists():
        raise HTTPException(status_code=404, detail="Audio not yet generated for this chapter")

    return FileResponse(
        audio_file,
        media_type="audio/wav",
        filename=audio_file.name,
        headers={
            "Accept-Ranges": "bytes",
            "Cache-Control": "no-cache",
        },
    )


@router.post("/book/{book_id}/parse", response_model=ParseBookResponse)
async def parse_book(
    book_id: int,
    request: ParseBookRequest,
    db: Session = Depends(get_db),
) -> ParseBookResponse:
    """Parse a book's preferred manuscript format and persist chapter text."""

    book = (
        db.query(Book)
        .options(selectinload(Book.chapters))
        .filter(Book.id == book_id)
        .first()
    )
    if book is None:
        raise HTTPException(status_code=404, detail=f"Book {book_id} not found")

    existing_chapter_count = len(book.chapters)
    if existing_chapter_count > 0 and not request.overwrite:
        return ParseBookResponse(
            status="already_parsed",
            chapters_detected=existing_chapter_count,
            message="Book already parsed. Set overwrite=True to re-parse.",
        )

    folder_path = Path(settings.FORMATTED_MANUSCRIPTS_PATH) / book.folder_path
    if not folder_path.exists():
        raise HTTPException(status_code=400, detail=f"Book folder not found: {book.folder_path}")

    try:
        metadata, chapters_data, manuscript_path = ManuscriptParserFactory.parse_manuscript(folder_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    chapters_data = _auto_split_parsed_chapters(chapters_data)

    if request.overwrite and existing_chapter_count > 0:
        for existing_chapter in list(book.chapters):
            db.delete(existing_chapter)
        db.flush()

    book.title = metadata.title
    book.subtitle = metadata.subtitle
    book.author = metadata.author
    book.narrator = get_application_settings().narrator_name
    book.status = BookStatus.PARSED

    opening_text = CreditsGenerator.generate_opening_credits(book.title, book.subtitle, book.author)
    db.add(
        Chapter(
            book_id=book_id,
            number=0,
            title="Opening Credits",
            type=ChapterType.OPENING_CREDITS,
            text_content=opening_text,
            word_count=len(opening_text.split()),
            status=ChapterStatus.PENDING,
        )
    )

    for sequence_number, parsed_chapter in enumerate(chapters_data, start=1):
        chapter_type = ChapterType(parsed_chapter.type)
        db.add(
            Chapter(
                book_id=book_id,
                number=sequence_number,
                title=parsed_chapter.title,
                type=chapter_type,
                text_content=parsed_chapter.raw_text,
                word_count=parsed_chapter.word_count,
                status=ChapterStatus.PENDING,
            )
        )

    closing_number = len(chapters_data) + 1
    closing_text = CreditsGenerator.generate_closing_credits(book.title, book.subtitle, book.author)
    db.add(
        Chapter(
            book_id=book_id,
            number=closing_number,
            title="Closing Credits",
            type=ChapterType.CLOSING_CREDITS,
            text_content=closing_text,
            word_count=len(closing_text.split()),
            status=ChapterStatus.PENDING,
        )
    )

    db.commit()
    invalidate_library_cache()
    logger.info("Parsed book %s from %s into %s segments", book_id, manuscript_path.name, closing_number + 1)

    return ParseBookResponse(
        status="parsing",
        chapters_detected=closing_number + 1,
        message=f"Successfully parsed {len(chapters_data)} narratable sections plus opening/closing credits.",
    )


@router.get("/book/{book_id}/parsed", response_model=list[ChapterResponse])
async def get_parsed_chapters(book_id: int, db: Session = Depends(get_db)) -> list[ChapterResponse]:
    """Return parsed chapter records, including their full text content."""

    book = db.query(Book).filter(Book.id == book_id).first()
    if book is None:
        raise HTTPException(status_code=404, detail=f"Book {book_id} not found")
    if book.status != BookStatus.PARSED:
        raise HTTPException(status_code=400, detail=f"Book not yet parsed. Status: {book.status}")

    chapters = db.query(Chapter).filter(Chapter.book_id == book_id).order_by(Chapter.number, Chapter.id).all()
    return [_serialize_chapter(chapter) for chapter in chapters]


@router.put("/book/{book_id}/chapter/{chapter_number}/text", response_model=ChapterResponse)
async def update_chapter_text(
    book_id: int,
    chapter_number: int,
    request: ChapterUpdateRequest,
    db: Session = Depends(get_db),
) -> ChapterResponse:
    """Update the stored text for a parsed chapter and refresh its word count."""

    if not request.text_content.strip():
        raise HTTPException(status_code=400, detail="Chapter text cannot be empty.")

    chapter = (
        db.query(Chapter)
        .filter(Chapter.book_id == book_id, Chapter.number == chapter_number)
        .first()
    )
    if chapter is None:
        raise HTTPException(status_code=404, detail=f"Chapter {chapter_number} not found in book {book_id}")

    chapter.text_content = request.text_content
    chapter.word_count = len(request.text_content.split())
    db.commit()

    logger.info("Updated chapter %s text for book %s", chapter_number, book_id)
    return _serialize_chapter(chapter)


@router.post("/book/{book_id}/chapter/{chapter_id}/split", response_model=list[ChapterResponse])
async def split_book_chapter(
    book_id: int,
    chapter_id: int,
    request: SplitChapterRequest,
    db: Session = Depends(get_db),
) -> list[ChapterResponse]:
    """Split one stored chapter at a paragraph boundary without auto-regenerating it."""

    book = db.query(Book).filter(Book.id == book_id).first()
    if book is None:
        raise HTTPException(status_code=404, detail=f"Book {book_id} not found")

    chapter = (
        db.query(Chapter)
        .filter(Chapter.book_id == book_id, Chapter.id == chapter_id)
        .first()
    )
    if chapter is None:
        raise HTTPException(status_code=404, detail=f"Chapter {chapter_id} not found in book {book_id}")

    left_chapter, right_chapter = _split_persisted_chapter(
        book=book,
        chapter=chapter,
        paragraph_index=request.paragraph_index,
        db=db,
    )
    db.commit()
    invalidate_library_cache()
    return [_serialize_chapter(left_chapter), _serialize_chapter(right_chapter)]


@router.get("/book/{book_id}/export-readiness", response_model=ExportReadinessResponse)
async def get_export_readiness(book_id: int, db: Session = Depends(get_db)) -> ExportReadinessResponse:
    """Return a consolidated pre-export QA and ACX readiness summary."""

    book = db.query(Book.id).filter(Book.id == book_id).first()
    if book is None:
        raise HTTPException(status_code=404, detail=f"Book {book_id} not found")

    return ExportReadinessResponse.model_validate(build_export_readiness_summary(book_id, db))
