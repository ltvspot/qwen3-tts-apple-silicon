"""Tests for batch QA approval and catalog summary APIs."""

from __future__ import annotations

from sqlalchemy.orm import Session

from src.database import (
    AudioQAResult,
    Book,
    BookStatus,
    Chapter,
    ChapterQARecord,
    ChapterStatus,
    ChapterType,
    QAAutomaticStatus,
    QAStatus,
)


def _create_book(test_db: Session, *, title: str, status: BookStatus = BookStatus.PARSED) -> Book:
    book = Book(
        title=title,
        author="QA Author",
        folder_path=title.lower().replace(" ", "-"),
        status=status,
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


def _create_audio_qa(test_db: Session, *, book_id: int, chapter_n: int, score: float) -> None:
    test_db.add(
        AudioQAResult(
            book_id=book_id,
            chapter_n=chapter_n,
            overall_score=score,
            report_json="{}",
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
    assert payload["unparsedBooks"] == 0
    assert payload["books_all_approved"] == 1
    assert payload["books_with_flags"] == 1
    assert payload["chapters_approved"] == 1
    assert payload["chapters_flagged"] == 1


def test_qa_metrics_excludes_unparsed_books(client, test_db: Session) -> None:
    """Unparsed books should be tracked separately instead of inflating pending QA counts."""

    for index in range(3):
        _create_book(test_db, title=f"Unparsed Book {index}", status=BookStatus.NOT_STARTED)

    for index in range(2):
        parsed_book = _create_book(test_db, title=f"Parsed Book {index}", status=BookStatus.PARSED)
        _create_chapter(test_db, book_id=parsed_book.id, number=1, qa_status=QAStatus.NOT_REVIEWED)

    response = client.get("/api/qa/catalog-summary")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_books"] == 5
    assert payload["unparsedBooks"] == 3
    assert payload["books_pending_qa"] == 2


def test_qa_metrics_includes_parsed_books(client, test_db: Session) -> None:
    """Parsed books with chapters should still appear in the pending QA counts."""

    parsed_book = _create_book(test_db, title="Pending QA Book", status=BookStatus.PARSED)
    _create_chapter(test_db, book_id=parsed_book.id, number=1, qa_status=QAStatus.NOT_REVIEWED)

    response = client.get("/api/qa/catalog-summary")

    assert response.status_code == 200
    payload = response.json()
    assert payload["unparsedBooks"] == 0
    assert payload["books_pending_qa"] == 1


def test_batch_approve_by_score_only_approves_chapters_above_threshold(client, test_db: Session) -> None:
    """Score-based approval should only approve chapters meeting the threshold."""

    book = _create_book(test_db, title="Score Approval Book")
    _create_chapter(test_db, book_id=book.id, number=1, qa_status=QAStatus.NOT_REVIEWED)
    _create_chapter(test_db, book_id=book.id, number=2, qa_status=QAStatus.NOT_REVIEWED)
    _create_qa_record(test_db, book_id=book.id, chapter_n=1, status=QAAutomaticStatus.PASS)
    _create_qa_record(test_db, book_id=book.id, chapter_n=2, status=QAAutomaticStatus.PASS)
    _create_audio_qa(test_db, book_id=book.id, chapter_n=1, score=88.0)
    _create_audio_qa(test_db, book_id=book.id, chapter_n=2, score=74.0)

    response = client.post("/api/qa/batch-approve", json={"book_id": book.id, "min_score": 80})

    assert response.status_code == 200
    assert response.json() == {"approved": 1, "below_threshold": 1, "already_approved": 0}


def test_batch_approve_all_by_score_returns_catalog_totals(client, test_db: Session) -> None:
    """Catalog-wide score approval should aggregate approvals across books."""

    first = _create_book(test_db, title="First Score Book")
    second = _create_book(test_db, title="Second Score Book")
    _create_chapter(test_db, book_id=first.id, number=1, qa_status=QAStatus.NOT_REVIEWED)
    _create_chapter(test_db, book_id=second.id, number=1, qa_status=QAStatus.APPROVED)
    _create_qa_record(test_db, book_id=first.id, chapter_n=1, status=QAAutomaticStatus.PASS)
    _create_qa_record(test_db, book_id=second.id, chapter_n=1, status=QAAutomaticStatus.PASS)
    _create_audio_qa(test_db, book_id=first.id, chapter_n=1, score=92.0)
    _create_audio_qa(test_db, book_id=second.id, chapter_n=1, score=95.0)

    response = client.post("/api/qa/batch-approve-all", json={"min_score": 85})

    assert response.status_code == 200
    assert response.json() == {"approved": 1, "below_threshold": 0, "already_approved": 1}


def test_batch_approve_requires_existing_book_for_score_mode(client, test_db: Session) -> None:
    """Score-based batch approval should reject missing books."""

    response = client.post("/api/qa/batch-approve", json={"book_id": 999, "min_score": 80})

    assert response.status_code == 404
    assert response.json()["detail"] == "Book 999 not found"
