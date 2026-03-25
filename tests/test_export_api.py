"""API tests for export job creation, status polling, and downloads."""

from __future__ import annotations

import json
from urllib.parse import unquote

from sqlalchemy.orm import Session

from src.api import export_routes
from src.database import (
    Book,
    BookExportStatus,
    BookStatus,
    Chapter,
    ChapterQARecord,
    ChapterStatus,
    ChapterType,
    ExportJob,
    QAAutomaticStatus,
    QAStatus,
    utc_now,
)
from src.pipeline.exporter import get_export_output_path


def _create_book(test_db: Session, *, title: str = "Export API Book") -> Book:
    """Create a persisted book row for export API tests."""

    book = Book(
        title=title,
        author="API Author",
        folder_path=title.lower().replace(" ", "-"),
        status=BookStatus.GENERATED,
    )
    test_db.add(book)
    test_db.commit()
    test_db.refresh(book)
    return book


def _create_ready_chapter(test_db: Session, *, book_id: int, number: int = 1) -> Chapter:
    """Create one generated and QA-approved chapter."""

    chapter = Chapter(
        book_id=book_id,
        number=number,
        title=f"Chapter {number}",
        type=ChapterType.CHAPTER,
        text_content="Ready for export.",
        word_count=4,
        status=ChapterStatus.GENERATED,
        qa_status=QAStatus.APPROVED,
        audio_path=f"exports/{book_id}/chapter-{number}.wav",
        duration_seconds=12.5,
    )
    test_db.add(chapter)
    test_db.commit()
    test_db.refresh(chapter)
    return chapter


def _create_generated_chapter(
    test_db: Session,
    *,
    book_id: int,
    number: int,
    qa_status: QAStatus = QAStatus.APPROVED,
) -> Chapter:
    """Create one generated chapter with configurable QA status."""

    chapter = Chapter(
        book_id=book_id,
        number=number,
        title=f"Chapter {number}",
        type=ChapterType.CHAPTER,
        text_content="Generated chapter text.",
        word_count=3,
        status=ChapterStatus.GENERATED,
        qa_status=qa_status,
        audio_path=f"exports/{book_id}/chapter-{number}.wav",
        duration_seconds=10.0,
    )
    test_db.add(chapter)
    test_db.commit()
    test_db.refresh(chapter)
    return chapter


def test_export_accepts_fully_generated_book(
    client,
    test_db: Session,
    monkeypatch,
) -> None:
    """Starting an export should persist one processing job row for the book."""

    book = _create_book(test_db)
    _create_ready_chapter(test_db, book_id=book.id)
    launched_jobs: list[int] = []

    monkeypatch.setattr(export_routes, "estimate_export_seconds", lambda *args, **kwargs: 120)
    monkeypatch.setattr(
        export_routes,
        "_launch_export_job",
        lambda export_job_id, session_factory=None: launched_jobs.append(export_job_id),
    )

    response = client.post(
        f"/api/book/{book.id}/export",
        json={
            "formats": ["mp3", "m4b"],
            "include_only_approved": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["book_id"] == book.id
    assert payload["export_status"] == "processing"
    assert payload["formats_requested"] == ["mp3", "m4b"]
    assert payload["expected_completion_seconds"] == 120
    assert payload["job_id"].startswith(f"export_{book.id}_")

    export_job = test_db.query(ExportJob).filter(ExportJob.book_id == book.id).one()
    test_db.refresh(book)
    assert launched_jobs == [export_job.id]
    assert export_job.export_status == BookExportStatus.PROCESSING
    assert json.loads(export_job.formats_requested) == ["mp3", "m4b"]
    assert export_job.progress_percent == 0.0
    assert export_job.current_stage == "Queued"
    assert book.export_status == BookExportStatus.PROCESSING


def test_export_rejects_partial_book(client, test_db: Session) -> None:
    """Exporting should fail when any chapter is still missing generated audio."""

    book = _create_book(test_db, title="Partial Export Book")
    _create_generated_chapter(test_db, book_id=book.id, number=1)
    test_db.add(
        Chapter(
            book_id=book.id,
            number=2,
            title="Chapter 2",
            type=ChapterType.CHAPTER,
            text_content="Still pending.",
            word_count=2,
            status=ChapterStatus.PENDING,
            qa_status=QAStatus.NOT_REVIEWED,
        )
    )
    test_db.commit()

    response = client.post(
        f"/api/book/{book.id}/export",
        json={
            "formats": ["mp3"],
            "include_only_approved": False,
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Only 1/2 chapters generated. Generate all chapters before exporting."


def test_export_rejects_unapproved_book(client, test_db: Session) -> None:
    """Exporting with approval required should fail until every chapter is approved."""

    book = _create_book(test_db, title="Unapproved Export Book")
    _create_generated_chapter(test_db, book_id=book.id, number=1, qa_status=QAStatus.APPROVED)
    _create_generated_chapter(test_db, book_id=book.id, number=2, qa_status=QAStatus.NOT_REVIEWED)

    response = client.post(
        f"/api/book/{book.id}/export",
        json={
            "formats": ["mp3"],
            "include_only_approved": True,
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Only 1/2 chapters approved. Approve all chapters before exporting."


def test_export_accepts_warning_chapter_when_ready_for_export(client, test_db: Session, monkeypatch) -> None:
    """Approval-only export should accept Gate 2 warnings that are still marked export-ready."""

    book = _create_book(test_db, title="Warning Ready Export Book")
    chapter = _create_generated_chapter(test_db, book_id=book.id, number=1, qa_status=QAStatus.NEEDS_REVIEW)
    test_db.add(
        ChapterQARecord(
            book_id=book.id,
            chapter_n=chapter.number,
            overall_status=QAAutomaticStatus.WARNING,
            qa_details=json.dumps(
                {
                    "chapter_n": chapter.number,
                    "book_id": book.id,
                    "overall_status": QAAutomaticStatus.WARNING.value,
                    "checks": [],
                    "chapter_report": {
                        "overall_grade": "B",
                        "ready_for_export": True,
                    },
                }
            ),
        )
    )
    test_db.commit()

    launched_jobs: list[int] = []
    monkeypatch.setattr(export_routes, "estimate_export_seconds", lambda *args, **kwargs: 120)
    monkeypatch.setattr(
        export_routes,
        "_launch_export_job",
        lambda export_job_id, session_factory=None: launched_jobs.append(export_job_id),
    )

    response = client.post(
        f"/api/book/{book.id}/export",
        json={
            "formats": ["mp3"],
            "include_only_approved": True,
        },
    )

    assert response.status_code == 200
    assert launched_jobs


def test_get_export_status_returns_completed_formats_and_qa_report(client, test_db: Session) -> None:
    """Status polling should deserialize the stored job state into API form."""

    book = _create_book(test_db, title="Completed Export Book")
    finished_at = utc_now()
    qa_report = {
        "book_id": book.id,
        "book_title": book.title,
        "export_date": finished_at.isoformat(),
        "chapters_included": 4,
        "chapters_approved": 3,
        "chapters_flagged": 1,
        "chapters_warnings": 2,
        "export_approved": False,
        "notes": "Synthetic QA report.",
        "chapter_summary": [
            {
                "chapter_n": 1,
                "chapter_title": "Chapter One",
                "status": "approved",
                "file_size_bytes": 1024,
                "duration_seconds": 12.4,
            },
        ],
    }

    test_db.add(
        ExportJob(
            book_id=book.id,
            job_token=f"export_{book.id}_20260324_143000",
            export_status=BookExportStatus.COMPLETED,
            formats_requested=json.dumps(["mp3", "m4b"]),
            format_details=json.dumps(
                {
                    "mp3": {
                        "status": "completed",
                        "file_size_bytes": 111,
                        "file_name": "Completed Export Book.mp3",
                        "download_url": f"/api/book/{book.id}/export/download/mp3",
                        "completed_at": finished_at.isoformat(),
                        "error_message": None,
                    },
                    "m4b": {
                        "status": "completed",
                        "file_size_bytes": 222,
                        "file_name": "Completed Export Book.m4b",
                        "download_url": f"/api/book/{book.id}/export/download/m4b",
                        "completed_at": finished_at.isoformat(),
                        "error_message": None,
                    },
                }
            ),
            include_only_approved=True,
            started_at=finished_at,
            completed_at=finished_at,
            qa_report=json.dumps(qa_report),
        )
    )
    book.export_status = BookExportStatus.COMPLETED
    book.last_export_date = finished_at
    test_db.commit()

    response = client.get(f"/api/book/{book.id}/export/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["book_id"] == book.id
    assert payload["export_status"] == "completed"
    assert payload["job_id"] == f"export_{book.id}_20260324_143000"
    assert payload["formats"]["mp3"]["file_size_bytes"] == 111
    assert payload["formats"]["m4b"]["download_url"] == f"/api/book/{book.id}/export/download/m4b"
    assert payload["qa_report"]["chapters_flagged"] == 1
    assert payload["qa_report"]["chapter_summary"][0]["chapter_title"] == "Chapter One"


def test_get_export_status_returns_live_progress_fields(client, test_db: Session) -> None:
    """Processing exports should expose real progress and stage metadata."""

    book = _create_book(test_db, title="In Flight Export")
    started_at = utc_now()

    test_db.add(
        ExportJob(
            book_id=book.id,
            job_token=f"export_{book.id}_20260324_150000",
            export_status=BookExportStatus.PROCESSING,
            formats_requested=json.dumps(["mp3"]),
            format_details=json.dumps({"mp3": {"status": "pending"}}),
            progress_percent=45.0,
            current_stage="Encoding MP3 (chapter 3/10)",
            current_format="mp3",
            current_chapter_n=3,
            total_chapters=10,
            include_only_approved=True,
            started_at=started_at,
        )
    )
    book.export_status = BookExportStatus.PROCESSING
    test_db.commit()

    response = client.get(f"/api/book/{book.id}/export/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["progress_percent"] == 45.0
    assert payload["current_stage"] == "Encoding MP3 (chapter 3/10)"
    assert payload["current_format"] == "mp3"
    assert payload["current_chapter_n"] == 3
    assert payload["total_chapters"] == 10


def test_batch_export_queues_ready_books_and_exposes_progress(client, test_db: Session, monkeypatch) -> None:
    """Batch export should queue eligible books and surface in-memory progress."""

    ready_book = _create_book(test_db, title="Ready For Batch Export")
    skipped_book = _create_book(test_db, title="Already Exported")
    not_ready_book = _create_book(test_db, title="Still Waiting")

    _create_ready_chapter(test_db, book_id=ready_book.id)
    _create_ready_chapter(test_db, book_id=skipped_book.id)
    test_db.add(
        Chapter(
            book_id=not_ready_book.id,
            number=1,
            title="Draft Chapter",
            type=ChapterType.CHAPTER,
            text_content="Pending QA.",
            word_count=2,
            status=ChapterStatus.PENDING,
            qa_status=QAStatus.NOT_REVIEWED,
        )
    )
    skipped_book.export_status = BookExportStatus.COMPLETED
    test_db.commit()

    launched_jobs: list[int] = []
    monkeypatch.setattr(export_routes, "estimate_export_seconds", lambda *args, **kwargs: 60)
    monkeypatch.setattr(
        export_routes,
        "_launch_export_job",
        lambda export_job_id, session_factory=None: launched_jobs.append(export_job_id),
    )

    response = client.post(
        "/api/export/batch",
        json={
            "formats": ["mp3"],
            "include_only_approved": True,
            "skip_already_exported": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["queued"] == 1
    assert response.json()["skipped"] == 1
    assert response.json()["not_ready"] == 1
    assert len(launched_jobs) == 1

    progress_response = client.get("/api/export/batch/progress")
    assert progress_response.status_code == 200
    progress = progress_response.json()
    assert progress["queued"] == 1
    assert progress["skipped"] == 1
    assert progress["not_ready"] == 1
    assert progress["books"][0]["title"] == "Ready For Batch Export"


def test_download_export_serves_audio_file(client, test_db: Session) -> None:
    """The download route should return the exported audiobook file with audio headers."""

    book = _create_book(test_db, title="Download Ready Book")
    output_path = get_export_output_path(book, "mp3")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(b"mp3-bytes")

    response = client.get(f"/api/book/{book.id}/export/download/mp3")

    assert response.status_code == 200
    assert response.content == b"mp3-bytes"
    assert response.headers["content-type"].startswith("audio/mpeg")
    assert "Download Ready Book.mp3" in unquote(response.headers["content-disposition"])
