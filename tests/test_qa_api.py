"""API tests for chapter QA retrieval, review, and dashboard summaries."""

from __future__ import annotations

from sqlalchemy.orm import Session

from src.database import Book, BookStatus, Chapter, ChapterStatus, ChapterType, ChapterQARecord, QAAutomaticStatus, QAManualStatus
from src.pipeline.qa_checker import QACheckResult, QAResult, apply_manual_review, persist_qa_result


def _create_book(test_db: Session, *, title: str, author: str) -> Book:
    """Create a parsed test book."""

    book = Book(
        title=title,
        author=author,
        folder_path=title.lower().replace(" ", "-"),
        status=BookStatus.PARSED,
    )
    test_db.add(book)
    test_db.commit()
    test_db.refresh(book)
    return book


def _create_chapter(test_db: Session, *, book_id: int, number: int, title: str) -> Chapter:
    """Create a generated test chapter."""

    chapter = Chapter(
        book_id=book_id,
        number=number,
        title=title,
        type=ChapterType.CHAPTER,
        text_content="Synthetic QA chapter text.",
        word_count=4,
        status=ChapterStatus.GENERATED,
        audio_path=f"{book_id}/chapters/{number:02d}.wav",
    )
    test_db.add(chapter)
    test_db.commit()
    test_db.refresh(chapter)
    return chapter


def _store_qa_result(
    test_db: Session,
    chapter: Chapter,
    *,
    overall_status: str,
    check_status: str | None = None,
) -> ChapterQARecord:
    """Persist a QA record for the provided chapter."""

    qa_result = QAResult(
        chapter_n=chapter.number,
        book_id=chapter.book_id,
        timestamp=chapter.updated_at,
        overall_status=overall_status,
        checks=[
            QACheckResult(
                name="file_exists",
                status="pass",
                message="File exists (100 bytes).",
                value=100,
            ),
            QACheckResult(
                name="silence_gaps",
                status=check_status or overall_status,
                message="Synthetic QA result.",
                value=4.2 if overall_status != "pass" else 0,
            ),
        ],
    )
    record = persist_qa_result(test_db, chapter, qa_result)
    test_db.commit()
    test_db.refresh(record)
    return record


def test_get_chapter_qa_returns_expected_shape(client, test_db: Session) -> None:
    """The chapter QA endpoint should serialize automatic and manual QA fields."""

    book = _create_book(test_db, title="QA API Book", author="Test Author")
    chapter = _create_chapter(test_db, book_id=book.id, number=1, title="Chapter One")
    record = _store_qa_result(test_db, chapter, overall_status="warning")
    assert record.overall_status == QAAutomaticStatus.WARNING

    response = client.get(f"/api/book/{book.id}/chapter/{chapter.number}/qa")

    assert response.status_code == 200
    payload = response.json()
    assert payload["book_id"] == book.id
    assert payload["chapter_n"] == chapter.number
    assert payload["overall_status"] == "warning"
    assert payload["manual_status"] is None
    assert payload["automatic_checks"][1]["name"] == "silence_gaps"


def test_post_chapter_qa_saves_manual_review(client, test_db: Session) -> None:
    """Manual QA review should persist and remain visible through the ORM row."""

    book = _create_book(test_db, title="Manual Review Book", author="Reviewer")
    chapter = _create_chapter(test_db, book_id=book.id, number=2, title="Review Chapter")
    _store_qa_result(test_db, chapter, overall_status="fail")

    response = client.post(
        f"/api/book/{book.id}/chapter/{chapter.number}/qa",
        json={
          "manual_status": "approved",
          "notes": "Listened through the affected section. Safe to ship.",
          "reviewed_by": "Tim",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["manual_status"] == "approved"
    assert payload["manual_reviewed_by"] == "Tim"

    record = test_db.query(ChapterQARecord).filter(ChapterQARecord.book_id == book.id).one()
    assert record.manual_status == QAManualStatus.APPROVED
    assert record.manual_notes == "Listened through the affected section. Safe to ship."


def test_get_qa_dashboard_returns_books_with_grouped_chapter_statuses(client, test_db: Session) -> None:
    """The QA dashboard should summarize book-level issues and pending review counts."""

    book_one = _create_book(test_db, title="Warning Book", author="Alexandria")
    chapter_one = _create_chapter(test_db, book_id=book_one.id, number=1, title="One")
    chapter_two = _create_chapter(test_db, book_id=book_one.id, number=2, title="Two")
    _store_qa_result(test_db, chapter_one, overall_status="pass")
    _store_qa_result(test_db, chapter_two, overall_status="warning")

    book_two = _create_book(test_db, title="Failure Book", author="Alexandria")
    chapter_three = _create_chapter(test_db, book_id=book_two.id, number=1, title="Three")
    _store_qa_result(test_db, chapter_three, overall_status="fail")
    apply_manual_review(test_db, chapter_three, QAManualStatus.FLAGGED, "Tim", "Needs regeneration.")
    test_db.commit()

    response = client.get("/api/qa/dashboard")

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["chapters_reviewed"] == 3
    assert payload["summary"]["chapters_pass"] == 1
    assert payload["summary"]["chapters_warning"] == 1
    assert payload["summary"]["chapters_fail"] == 1
    assert payload["summary"]["chapters_pending_manual"] == 1

    warning_book = next(book for book in payload["books_needing_review"] if book["book_id"] == book_one.id)
    assert warning_book["chapters_warning"] == 1
    assert warning_book["chapters_pending_manual"] == 1
    assert warning_book["chapters"][1]["overall_status"] == "warning"

    failure_book = next(book for book in payload["books_needing_review"] if book["book_id"] == book_two.id)
    assert failure_book["chapters_fail"] == 1
    assert failure_book["chapters"][0]["manual_status"] == "flagged"
