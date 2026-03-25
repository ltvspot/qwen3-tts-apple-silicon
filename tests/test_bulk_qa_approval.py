"""Tests for per-book bulk approval of fully passing QA chapters."""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from src.database import (
    Book,
    BookStatus,
    Chapter,
    ChapterQARecord,
    ChapterStatus,
    ChapterType,
    QAAutomaticStatus,
    QAManualStatus,
    QAStatus,
)


def _create_book(test_db: Session) -> Book:
    book = Book(
        title="Bulk QA Approval Book",
        author="QA Reviewer",
        folder_path="bulk-qa-approval-book",
        status=BookStatus.PARSED,
    )
    test_db.add(book)
    test_db.commit()
    test_db.refresh(book)
    return book


def _create_chapter(test_db: Session, *, book_id: int, number: int, qa_status: QAStatus) -> Chapter:
    chapter = Chapter(
        book_id=book_id,
        number=number,
        title=f"Chapter {number}",
        type=ChapterType.CHAPTER,
        text_content="Synthetic QA content.",
        word_count=3,
        status=ChapterStatus.GENERATED,
        qa_status=qa_status,
    )
    test_db.add(chapter)
    test_db.commit()
    test_db.refresh(chapter)
    return chapter


def _create_record(
    test_db: Session,
    *,
    book_id: int,
    chapter_n: int,
    overall_status: QAAutomaticStatus,
    manual_status: QAManualStatus | None = None,
) -> None:
    test_db.add(
        ChapterQARecord(
            book_id=book_id,
            chapter_n=chapter_n,
            overall_status=overall_status,
            qa_details=json.dumps({"chapter_n": chapter_n, "overall_status": overall_status.value, "checks": []}),
            manual_status=manual_status,
        )
    )
    test_db.commit()


def test_approve_all_passing_only_approves_clean_chapters(client, test_db: Session) -> None:
    """Only fully passing and not-yet-reviewed chapters should be auto-approved."""

    book = _create_book(test_db)
    approved_target = _create_chapter(test_db, book_id=book.id, number=1, qa_status=QAStatus.NOT_REVIEWED)
    warning_chapter = _create_chapter(test_db, book_id=book.id, number=2, qa_status=QAStatus.NEEDS_REVIEW)
    flagged_pass = _create_chapter(test_db, book_id=book.id, number=3, qa_status=QAStatus.NEEDS_REVIEW)

    _create_record(test_db, book_id=book.id, chapter_n=1, overall_status=QAAutomaticStatus.PASS)
    _create_record(test_db, book_id=book.id, chapter_n=2, overall_status=QAAutomaticStatus.WARNING)
    _create_record(
        test_db,
        book_id=book.id,
        chapter_n=3,
        overall_status=QAAutomaticStatus.PASS,
        manual_status=QAManualStatus.FLAGGED,
    )

    response = client.post(f"/api/book/{book.id}/approve-all-passing")

    assert response.status_code == 200
    assert response.json() == {"approved": 1}

    approved_record = (
        test_db.query(ChapterQARecord)
        .filter(ChapterQARecord.book_id == book.id, ChapterQARecord.chapter_n == approved_target.number)
        .one()
    )
    assert approved_record.manual_status == QAManualStatus.APPROVED
    assert approved_record.manual_reviewed_by == "auto-approved"

    warning_record = (
        test_db.query(ChapterQARecord)
        .filter(ChapterQARecord.book_id == book.id, ChapterQARecord.chapter_n == warning_chapter.number)
        .one()
    )
    assert warning_record.manual_status is None

    flagged_record = (
        test_db.query(ChapterQARecord)
        .filter(ChapterQARecord.book_id == book.id, ChapterQARecord.chapter_n == flagged_pass.number)
        .one()
    )
    assert flagged_record.manual_status == QAManualStatus.FLAGGED
