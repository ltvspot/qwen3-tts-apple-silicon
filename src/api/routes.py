"""FastAPI routes for library indexing and manuscript parsing."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session, selectinload

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
from src.parser import CreditsGenerator, DocxParser

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


class ParseBookRequest(BaseModel):
    """Request payload for parsing a book's DOCX manuscript."""

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


router = APIRouter(prefix="/api", tags=["library"])


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


def _build_library_stats(db: Session) -> LibraryStats:
    """Return aggregate counts for the whole indexed library."""

    counts = {status.value: 0 for status in BookStatus}
    for row in db.query(Book.status).all():
        if row[0] is not None:
            counts[row[0].value if isinstance(row[0], BookStatus) else str(row[0])] += 1
    return LibraryStats(**counts)


@router.post("/library/scan", response_model=LibraryScanResponse)
async def scan_library(db: Session = Depends(get_db)) -> LibraryScanResponse:
    """Scan the manuscript root and index any newly discovered book folders."""

    scanner = LibraryScanner()
    result = scanner.scan(db)
    db.commit()
    logger.info(
        "Library scan complete: found=%s indexed=%s new=%s errors=%s",
        result["total_found"],
        result["total_indexed"],
        result["new_books"],
        len(result["errors"]),
    )
    return LibraryScanResponse(**result)


@router.get("/library", response_model=LibraryResponse)
async def get_library(
    status_filter: BookStatus | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> LibraryResponse:
    """Return paginated library books plus lifecycle statistics."""

    query = db.query(Book).options(selectinload(Book.chapters)).order_by(Book.created_at, Book.id)
    if status_filter is not None:
        query = query.filter(Book.status == status_filter)

    total = query.count()
    books = query.offset(offset).limit(limit).all()

    return LibraryResponse(
        total=total,
        books=[_serialize_book(book) for book in books],
        stats=_build_library_stats(db),
    )


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


@router.get("/book/{book_id}/chapters", response_model=list[ChapterResponse])
async def get_book_chapters(book_id: int, db: Session = Depends(get_db)) -> list[ChapterResponse]:
    """Return all stored chapters for a book in narration order."""

    book = db.query(Book.id).filter(Book.id == book_id).first()
    if book is None:
        raise HTTPException(status_code=404, detail=f"Book {book_id} not found")

    chapters = db.query(Chapter).filter(Chapter.book_id == book_id).order_by(Chapter.number, Chapter.id).all()
    return [_serialize_chapter(chapter) for chapter in chapters]


@router.post("/book/{book_id}/parse", response_model=ParseBookResponse)
async def parse_book(
    book_id: int,
    request: ParseBookRequest,
    db: Session = Depends(get_db),
) -> ParseBookResponse:
    """Parse a book's preferred DOCX manuscript and persist chapter text."""

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

    scanner = LibraryScanner()
    folder_path = Path(settings.FORMATTED_MANUSCRIPTS_PATH) / book.folder_path
    if not folder_path.exists():
        raise HTTPException(status_code=400, detail=f"Book folder not found: {book.folder_path}")

    docx_path = scanner._find_docx_file(folder_path)
    if docx_path is None:
        raise HTTPException(status_code=400, detail=f"No DOCX file found in {book.folder_path}")

    parser = DocxParser()
    try:
        metadata, chapters_data = parser.parse(docx_path)
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
    logger.info("Parsed book %s from %s into %s segments", book_id, docx_path.name, closing_number + 1)

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
