"""API routes for automated and manual chapter QA."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from src.database import (
    Book,
    BookStatus,
    Chapter,
    ChapterQARecord,
    QAAutomaticStatus,
    QAManualStatus,
    QAStatus,
    get_db,
    utc_now,
)
from src.pipeline.qa_checker import apply_manual_review, build_qa_record_response

router = APIRouter(prefix="/api", tags=["qa"])


class QACheckResponse(BaseModel):
    """Serialized automatic QA check result."""

    name: str
    status: str
    message: str
    value: float | None = None


class ChapterQAResponse(BaseModel):
    """Detailed QA payload for one chapter."""

    chapter_n: int
    book_id: int
    overall_status: str
    automatic_checks: list[QACheckResponse]
    checked_at: datetime
    manual_status: str | None = None
    manual_notes: str | None = None
    manual_reviewed_by: str | None = None
    manual_reviewed_at: datetime | None = None


class ManualQAReviewRequest(BaseModel):
    """Request payload for recording a manual QA decision."""

    manual_status: QAManualStatus
    notes: str | None = None
    reviewed_by: str = Field(min_length=1, max_length=255)


class ManualQAReviewResponse(BaseModel):
    """Serialized manual review confirmation payload."""

    chapter_n: int
    book_id: int
    manual_status: str
    manual_notes: str | None = None
    manual_reviewed_by: str
    manual_reviewed_at: datetime


class DashboardChapterResponse(BaseModel):
    """Dashboard QA entry for one chapter."""

    book_id: int
    chapter_n: int
    chapter_title: str | None = None
    chapter_type: str | None = None
    overall_status: str
    automatic_checks: list[QACheckResponse]
    checked_at: datetime
    manual_status: str | None = None
    manual_notes: str | None = None
    manual_reviewed_by: str | None = None
    manual_reviewed_at: datetime | None = None
    audio_url: str | None = None


class DashboardBookResponse(BaseModel):
    """Dashboard summary and nested chapter list for one book."""

    model_config = ConfigDict(from_attributes=True)

    book_id: int
    book_title: str
    book_author: str
    chapters_total: int
    chapters_pass: int
    chapters_warning: int
    chapters_fail: int
    chapters_pending_manual: int
    overall_book_status: str
    latest_checked_at: datetime | None = None
    chapters: list[DashboardChapterResponse]


class DashboardSummaryResponse(BaseModel):
    """Top-line QA counters for the dashboard."""

    books_reviewed: int
    chapters_reviewed: int
    chapters_pass: int
    chapters_warning: int
    chapters_fail: int
    chapters_pending_manual: int


class QADashboardResponse(BaseModel):
    """Full QA dashboard payload."""

    books_needing_review: list[DashboardBookResponse]
    summary: DashboardSummaryResponse


class BatchApproveResponse(BaseModel):
    """Batch QA approval response."""

    approved: int
    skipped: int
    flagged: int


class CatalogQASummaryResponse(BaseModel):
    """Catalog-wide QA summary."""

    total_books: int
    unparsed_books: int = Field(serialization_alias="unparsedBooks")
    books_all_approved: int
    books_with_flags: int
    books_pending_qa: int
    total_chapters: int
    chapters_approved: int
    chapters_flagged: int
    chapters_pending: int


def _load_chapter_or_404(book_id: int, chapter_n: int, db: Session) -> Chapter:
    """Load a chapter row or raise a 404."""

    chapter = (
        db.query(Chapter)
        .filter(Chapter.book_id == book_id, Chapter.number == chapter_n)
        .first()
    )
    if chapter is None:
        raise HTTPException(status_code=404, detail=f"Chapter {chapter_n} not found in book {book_id}")
    return chapter


def _load_qa_record_or_404(book_id: int, chapter_n: int, db: Session) -> ChapterQARecord:
    """Load a QA record row or raise a 404."""

    record = (
        db.query(ChapterQARecord)
        .filter(ChapterQARecord.book_id == book_id, ChapterQARecord.chapter_n == chapter_n)
        .first()
    )
    if record is None:
        raise HTTPException(status_code=404, detail=f"QA results not found for chapter {chapter_n} in book {book_id}")
    return record


def _book_status_rank(status: str) -> int:
    """Return a stable priority for status-based sorting."""

    return {"fail": 2, "warning": 1, "pass": 0}.get(status, 0)


def _chapter_needs_manual_review(record: ChapterQARecord) -> bool:
    """Return True when the automatic QA result still requires a human decision."""

    return (
        record.manual_status is None
        and record.overall_status in {QAAutomaticStatus.WARNING, QAAutomaticStatus.FAIL}
    )


def _serialize_dashboard_chapter(record: ChapterQARecord, chapter: Chapter | None) -> DashboardChapterResponse:
    """Convert a QA record plus chapter metadata into dashboard form."""

    payload = build_qa_record_response(record)
    return DashboardChapterResponse(
        book_id=record.book_id,
        chapter_n=record.chapter_n,
        chapter_title=chapter.title if chapter is not None else None,
        chapter_type=chapter.type.value if chapter is not None else None,
        overall_status=payload["overall_status"],
        automatic_checks=[QACheckResponse(**check) for check in payload["automatic_checks"]],
        checked_at=payload["checked_at"],
        manual_status=payload["manual_status"],
        manual_notes=payload["manual_notes"],
        manual_reviewed_by=payload["manual_reviewed_by"],
        manual_reviewed_at=payload["manual_reviewed_at"],
        audio_url=(
            f"/api/book/{record.book_id}/chapter/{record.chapter_n}/audio"
            if chapter is not None and chapter.audio_path
            else None
        ),
    )


def _group_dashboard_books(
    records: list[ChapterQARecord],
    chapter_lookup: dict[tuple[int, int], Chapter],
    book_lookup: dict[int, Book],
    *,
    status_filter: str | None,
) -> list[DashboardBookResponse]:
    """Group QA records by book and compute book-level QA summaries."""

    records_by_book: dict[int, list[ChapterQARecord]] = {}
    for record in records:
        records_by_book.setdefault(record.book_id, []).append(record)

    books: list[DashboardBookResponse] = []
    for book_id, book_records in records_by_book.items():
        book = book_lookup.get(book_id)
        if book is None:
            continue
        sorted_records = sorted(book_records, key=lambda record: record.chapter_n)
        pending_manual = [record for record in sorted_records if _chapter_needs_manual_review(record)]

        if status_filter == "pending_review" and not pending_manual:
            continue
        if status_filter in {"pass", "warning", "fail"}:
            matching = [record for record in sorted_records if record.overall_status.value == status_filter]
            if not matching:
                continue

        chapters_pass = sum(record.overall_status == QAAutomaticStatus.PASS for record in sorted_records)
        chapters_warning = sum(record.overall_status == QAAutomaticStatus.WARNING for record in sorted_records)
        chapters_fail = sum(record.overall_status == QAAutomaticStatus.FAIL for record in sorted_records)
        overall_status = max(
            (record.overall_status.value for record in sorted_records),
            key=_book_status_rank,
            default="pass",
        )
        latest_checked_at = max((record.checked_at for record in sorted_records), default=None)

        books.append(
            DashboardBookResponse(
                book_id=book_id,
                book_title=book.title,
                book_author=book.author,
                chapters_total=len(sorted_records),
                chapters_pass=chapters_pass,
                chapters_warning=chapters_warning,
                chapters_fail=chapters_fail,
                chapters_pending_manual=len(pending_manual),
                overall_book_status=overall_status,
                latest_checked_at=latest_checked_at,
                chapters=[
                    _serialize_dashboard_chapter(record, chapter_lookup.get((record.book_id, record.chapter_n)))
                    for record in sorted_records
                ],
            )
        )

    return books


@router.get("/book/{book_id}/chapter/{chapter_n}/qa", response_model=ChapterQAResponse)
async def get_chapter_qa(book_id: int, chapter_n: int, db: Session = Depends(get_db)) -> ChapterQAResponse:
    """Return stored automatic and manual QA details for one chapter."""

    _load_chapter_or_404(book_id, chapter_n, db)
    record = _load_qa_record_or_404(book_id, chapter_n, db)
    return ChapterQAResponse(**build_qa_record_response(record))


@router.post("/book/{book_id}/chapter/{chapter_n}/qa", response_model=ManualQAReviewResponse)
async def review_chapter_qa(
    book_id: int,
    chapter_n: int,
    request: ManualQAReviewRequest,
    db: Session = Depends(get_db),
) -> ManualQAReviewResponse:
    """Store a manual chapter QA decision."""

    chapter = _load_chapter_or_404(book_id, chapter_n, db)
    _load_qa_record_or_404(book_id, chapter_n, db)

    updated_record = apply_manual_review(
        db,
        chapter,
        request.manual_status,
        request.reviewed_by,
        request.notes,
    )
    db.commit()

    return ManualQAReviewResponse(
        chapter_n=chapter_n,
        book_id=book_id,
        manual_status=updated_record.manual_status.value if updated_record.manual_status is not None else "",
        manual_notes=updated_record.manual_notes,
        manual_reviewed_by=updated_record.manual_reviewed_by or request.reviewed_by,
        manual_reviewed_at=updated_record.manual_reviewed_at or utc_now(),
    )


@router.get("/qa/dashboard", response_model=QADashboardResponse)
async def get_qa_dashboard(
    status: str | None = Query(default=None),
    book_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> QADashboardResponse:
    """Return grouped QA dashboard data for reviewed chapters."""

    if status not in {None, "pass", "warning", "fail", "pending_review"}:
        raise HTTPException(status_code=400, detail="Unsupported QA dashboard status filter.")

    records_query = db.query(ChapterQARecord)
    if book_id is not None:
        records_query = records_query.filter(ChapterQARecord.book_id == book_id)

    records = records_query.order_by(ChapterQARecord.checked_at.desc(), ChapterQARecord.chapter_n.asc()).all()
    if not records:
        return QADashboardResponse(
            books_needing_review=[],
            summary=DashboardSummaryResponse(
                books_reviewed=0,
                chapters_reviewed=0,
                chapters_pass=0,
                chapters_warning=0,
                chapters_fail=0,
                chapters_pending_manual=0,
            ),
        )

    book_ids = {record.book_id for record in records}
    book_lookup = {
        book.id: book
        for book in db.query(Book).filter(Book.id.in_(book_ids)).all()
    }
    chapter_lookup = {
        (chapter.book_id, chapter.number): chapter
        for chapter in db.query(Chapter).filter(Chapter.book_id.in_(book_ids)).all()
    }

    grouped_books = _group_dashboard_books(
        records,
        chapter_lookup,
        book_lookup,
        status_filter=status,
    )
    grouped_books.sort(
        key=lambda book: (
            -book.chapters_pending_manual,
            -_book_status_rank(book.overall_book_status),
            (book.latest_checked_at or datetime.min).timestamp() if book.latest_checked_at else 0,
            book.book_title.lower(),
        )
    )

    summary_records = [
        record
        for record in records
        if status is None
        or (status == "pending_review" and _chapter_needs_manual_review(record))
        or record.overall_status.value == status
    ]

    return QADashboardResponse(
        books_needing_review=grouped_books[:limit],
        summary=DashboardSummaryResponse(
            books_reviewed=len({book.book_id for book in grouped_books}),
            chapters_reviewed=len(summary_records),
            chapters_pass=sum(record.overall_status == QAAutomaticStatus.PASS for record in summary_records),
            chapters_warning=sum(record.overall_status == QAAutomaticStatus.WARNING for record in summary_records),
            chapters_fail=sum(record.overall_status == QAAutomaticStatus.FAIL for record in summary_records),
            chapters_pending_manual=sum(_chapter_needs_manual_review(record) for record in summary_records),
        ),
    )


def _batch_approve_records(
    records: list[ChapterQARecord],
    chapter_lookup: dict[tuple[int, int], Chapter],
    *,
    approve_warnings: bool,
    db: Session,
) -> BatchApproveResponse:
    """Approve all eligible QA records and return aggregate counts."""

    approved = 0
    skipped = 0
    flagged = 0
    allowed_statuses = {QAAutomaticStatus.PASS}
    if approve_warnings:
        allowed_statuses.add(QAAutomaticStatus.WARNING)

    for record in records:
        chapter = chapter_lookup.get((record.book_id, record.chapter_n))
        if chapter is None:
            skipped += 1
            continue

        if record.manual_status == QAManualStatus.APPROVED or chapter.qa_status == QAStatus.APPROVED:
            skipped += 1
            continue

        if record.manual_status == QAManualStatus.FLAGGED or record.overall_status == QAAutomaticStatus.FAIL:
            flagged += 1
            continue

        if record.overall_status not in allowed_statuses:
            skipped += 1
            continue

        apply_manual_review(
            db,
            chapter,
            QAManualStatus.APPROVED,
            reviewed_by="Batch QA",
            notes=record.manual_notes,
        )
        approved += 1

    db.commit()
    return BatchApproveResponse(approved=approved, skipped=skipped, flagged=flagged)


@router.post("/qa/batch-approve/{book_id}", response_model=BatchApproveResponse)
async def batch_approve_book(
    book_id: int,
    approve_warnings: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> BatchApproveResponse:
    """Approve all eligible QA results for one book."""

    book = db.query(Book).filter(Book.id == book_id).first()
    if book is None:
        raise HTTPException(status_code=404, detail=f"Book {book_id} not found")

    records = (
        db.query(ChapterQARecord)
        .filter(ChapterQARecord.book_id == book_id)
        .order_by(ChapterQARecord.chapter_n.asc())
        .all()
    )
    chapter_lookup = {
        (chapter.book_id, chapter.number): chapter
        for chapter in db.query(Chapter).filter(Chapter.book_id == book_id).all()
    }
    return _batch_approve_records(records, chapter_lookup, approve_warnings=approve_warnings, db=db)


@router.post("/qa/batch-approve-all", response_model=BatchApproveResponse)
async def batch_approve_all(
    approve_warnings: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> BatchApproveResponse:
    """Approve all eligible QA results across the full catalog."""

    records = db.query(ChapterQARecord).order_by(ChapterQARecord.book_id.asc(), ChapterQARecord.chapter_n.asc()).all()
    chapter_lookup = {
        (chapter.book_id, chapter.number): chapter
        for chapter in db.query(Chapter).all()
    }
    return _batch_approve_records(records, chapter_lookup, approve_warnings=approve_warnings, db=db)


@router.get("/qa/catalog-summary", response_model=CatalogQASummaryResponse)
async def get_catalog_qa_summary(db: Session = Depends(get_db)) -> CatalogQASummaryResponse:
    """Return catalog-wide QA summary counts."""

    books = db.query(Book).all()
    chapters = db.query(Chapter).all()
    qa_ready_statuses = {
        BookStatus.PARSED,
        BookStatus.GENERATED,
        BookStatus.EXPORTED,
    }

    chapters_by_book: dict[int, list[Chapter]] = {}
    for chapter in chapters:
        chapters_by_book.setdefault(chapter.book_id, []).append(chapter)

    books_all_approved = 0
    books_with_flags = 0
    books_pending_qa = 0
    unparsed_count = sum(book.status not in qa_ready_statuses for book in books)

    for book in books:
        if book.status not in qa_ready_statuses:
            continue
        book_chapters = chapters_by_book.get(book.id, [])
        if not book_chapters:
            books_pending_qa += 1
            continue

        if all(chapter.qa_status == QAStatus.APPROVED for chapter in book_chapters):
            books_all_approved += 1
        elif any(chapter.qa_status == QAStatus.NEEDS_REVIEW for chapter in book_chapters):
            books_with_flags += 1
        else:
            books_pending_qa += 1

    chapters_approved = sum(chapter.qa_status == QAStatus.APPROVED for chapter in chapters)
    chapters_flagged = sum(chapter.qa_status == QAStatus.NEEDS_REVIEW for chapter in chapters)
    chapters_pending = len(chapters) - chapters_approved - chapters_flagged

    return CatalogQASummaryResponse(
        total_books=len(books),
        unparsed_books=unparsed_count,
        books_all_approved=books_all_approved,
        books_with_flags=books_with_flags,
        books_pending_qa=books_pending_qa,
        total_chapters=len(chapters),
        chapters_approved=chapters_approved,
        chapters_flagged=chapters_flagged,
        chapters_pending=chapters_pending,
    )
