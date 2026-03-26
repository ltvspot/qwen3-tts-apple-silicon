"""FastAPI routes for library indexing and manuscript parsing."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session, selectinload

from src.api.cache import invalidate_library_cache, library_cache
from src.api.library import LibraryScanner
from src.config import get_application_settings, settings
from src.database import (
    Book,
    BookGenerationStatus,
    BookStatus,
    Chapter,
    ChapterStatus,
    ChapterType,
    QAStatus,
    get_db,
)
from src.pipeline.manuscript_validator import ManuscriptValidationReport, ManuscriptValidator
from src.pipeline.pronunciation_watchlist import PronunciationWatchlist
from src.parser import CreditsGenerator, ManuscriptParserFactory

logger = logging.getLogger(__name__)


class BookResponse(BaseModel):
    """Response model for a book."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    subtitle: str | None
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
