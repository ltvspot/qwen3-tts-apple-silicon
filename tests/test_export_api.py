"""API tests for export job creation, status polling, and downloads."""

from __future__ import annotations

import json
from urllib.parse import unquote

from sqlalchemy.orm import Session

from src.api import export_routes
from src.database import Book, BookExportStatus, BookStatus, ExportJob, utc_now
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


def test_post_export_creates_processing_job_and_returns_queue_payload(
    client,
    test_db: Session,
    monkeypatch,
) -> None:
    """Starting an export should persist one processing job row for the book."""

    book = _create_book(test_db)
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
    assert book.export_status == BookExportStatus.PROCESSING


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
