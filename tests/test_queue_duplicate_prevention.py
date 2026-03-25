"""Tests for duplicate generation-job prevention."""

from __future__ import annotations

from sqlalchemy.orm import Session

from src.database import (
    Book,
    BookStatus,
    Chapter,
    ChapterStatus,
    ChapterType,
    GenerationJob,
    GenerationJobStatus,
    GenerationJobType,
)


def _create_book(test_db: Session) -> Book:
    book = Book(
        title="Duplicate Queue Book",
        author="Queue Author",
        folder_path="duplicate-queue-book",
        status=BookStatus.PARSED,
    )
    test_db.add(book)
    test_db.commit()
    test_db.refresh(book)
    return book


def _create_chapter(test_db: Session, book_id: int, number: int = 1) -> Chapter:
    chapter = Chapter(
        book_id=book_id,
        number=number,
        title=f"Chapter {number}",
        type=ChapterType.CHAPTER,
        text_content="This chapter is ready for narration.",
        word_count=6,
        status=ChapterStatus.PENDING,
    )
    test_db.add(chapter)
    test_db.commit()
    test_db.refresh(chapter)
    return chapter


def _create_job(test_db: Session, *, book_id: int, chapter_id: int | None, status: GenerationJobStatus) -> None:
    test_db.add(
        GenerationJob(
            book_id=book_id,
            chapter_id=chapter_id,
            job_type=GenerationJobType.SINGLE_CHAPTER if chapter_id is not None else GenerationJobType.FULL_BOOK,
            status=status,
            progress=0.0,
            current_chapter_progress=0.0,
            chapters_total=1,
            chapters_completed=0,
            chapters_failed=0,
            current_chapter_n=1,
            force=False,
        )
    )
    test_db.commit()


def test_book_generation_duplicate_returns_409(client, test_db: Session) -> None:
    """A second queued or running book job should be rejected as a conflict."""

    book = _create_book(test_db)
    _create_chapter(test_db, book.id)
    _create_job(test_db, book_id=book.id, chapter_id=None, status=GenerationJobStatus.QUEUED)

    response = client.post(f"/api/book/{book.id}/generate")

    assert response.status_code == 409
    assert "queued or running" in response.json()["detail"]


def test_chapter_generation_duplicate_returns_409(client, test_db: Session) -> None:
    """A single-chapter request should also reject duplicates for the same book."""

    book = _create_book(test_db)
    chapter = _create_chapter(test_db, book.id)
    _create_job(test_db, book_id=book.id, chapter_id=chapter.id, status=GenerationJobStatus.RUNNING)

    response = client.post(f"/api/book/{book.id}/chapter/{chapter.number}/generate")

    assert response.status_code == 409
    assert "queued or running" in response.json()["detail"]
