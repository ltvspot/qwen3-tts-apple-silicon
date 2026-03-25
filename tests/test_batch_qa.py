"""Tests for batch QA approval and catalog summary APIs."""

from __future__ import annotations

from sqlalchemy.orm import Session

from src.database import (
    Book,
    Chapter,
    ChapterQARecord,
    ChapterStatus,
    ChapterType,
    QAAutomaticStatus,
    QAStatus,
)


def _create_book(test_db: Session, *, title: str) -> Book:
    book = Book(
        title=title,
        author="QA Author",
        folder_path=title.lower().replace(" ", "-"),
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
        text_content="chapter text",
        word_count=10,
        status=ChapterStatus.GENERATED,
        qa_status=qa_status,
    )
    test_db.add(chapter)
    test_db.commit()
    test_db.refresh(chapter)
    return chapter


def _create_qa_record(test_db: Session, *, book_id: int, chapter_n: int, status: QAAutomaticStatus) -> None:
    test_db.add(
        ChapterQARecord(
            book_id=book_id,
            chapter_n=chapter_n,
            overall_status=status,
            qa_details='{"overall_status":"pass","checks":[]}',
        )
    )
    test_db.commit()


def test_batch_approve_book_and_catalog_summary(client, test_db: Session) -> None:
    """Batch QA routes should approve passing chapters and summarize the catalog."""

    approved_book = _create_book(test_db, title="Approved Book")
    flagged_book = _create_book(test_db, title="Flagged Book")

    _create_chapter(test_db, book_id=approved_book.id, number=1, qa_status=QAStatus.NOT_REVIEWED)
    _create_qa_record(test_db, book_id=approved_book.id, chapter_n=1, status=QAAutomaticStatus.PASS)

    _create_chapter(test_db, book_id=flagged_book.id, number=1, qa_status=QAStatus.NEEDS_REVIEW)
    _create_qa_record(test_db, book_id=flagged_book.id, chapter_n=1, status=QAAutomaticStatus.FAIL)

    approve_response = client.post(f"/api/qa/batch-approve/{approved_book.id}")
    assert approve_response.status_code == 200
    assert approve_response.json() == {"approved": 1, "flagged": 0, "skipped": 0}

    summary_response = client.get("/api/qa/catalog-summary")
    assert summary_response.status_code == 200
    payload = summary_response.json()
    assert payload["total_books"] == 2
    assert payload["books_all_approved"] == 1
    assert payload["books_with_flags"] == 1
    assert payload["chapters_approved"] == 1
    assert payload["chapters_flagged"] == 1
