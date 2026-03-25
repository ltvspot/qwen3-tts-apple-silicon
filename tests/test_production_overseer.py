"""Tests for the Production Overseer dashboard and reporting APIs."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from src.api import overseer_routes
from src.config import settings
from src.database import (
    Book,
    BookStatus,
    Chapter,
    ChapterStatus,
    ChapterType,
    ChapterQARecord,
    QAAutomaticStatus,
    QualitySnapshot,
)
from src.pipeline.manuscript_validator import ManuscriptValidator
from src.pipeline.qa_checker import QACheckResult, QAResult, persist_qa_result
from src.pipeline.quality_tracker import BookQualitySnapshot, QualityTracker


def _create_book(test_db: Session, *, title: str = "Test Book", author: str = "Alexandria") -> Book:
    book = Book(
        title=title,
        author=author,
        narrator="Kent Zimering",
        folder_path=title.lower().replace(" ", "-"),
        status=BookStatus.PARSED,
    )
    test_db.add(book)
    test_db.commit()
    test_db.refresh(book)
    return book


def _create_chapter(
    test_db: Session,
    *,
    book_id: int,
    number: int,
    title: str,
    text: str = "Synthetic chapter text for overseer tests.",
    status: ChapterStatus = ChapterStatus.GENERATED,
    mastered: bool = True,
    generation_metadata: dict | None = None,
) -> Chapter:
    relative_audio_path = Path(f"{book_id}/chapters/{number:02d}.wav")
    absolute_audio_path = (Path(settings.OUTPUTS_PATH) / relative_audio_path).resolve()
    absolute_audio_path.parent.mkdir(parents=True, exist_ok=True)
    chapter = Chapter(
        book_id=book_id,
        number=number,
        title=title,
        type=ChapterType.CHAPTER,
        text_content=text,
        word_count=len(text.split()),
        status=status,
        audio_path=str(relative_audio_path),
        duration_seconds=30.0,
        mastered=mastered,
        generation_metadata=json.dumps(generation_metadata) if generation_metadata is not None else None,
    )
    test_db.add(chapter)
    test_db.commit()
    test_db.refresh(chapter)
    return chapter


def _store_qa_record(
    test_db: Session,
    chapter: Chapter,
    *,
    overall_status: str = "pass",
    qa_grade: str = "A",
    ready_for_export: bool = True,
    manual_notes: str | None = None,
) -> ChapterQARecord:
    qa_result = QAResult(
        chapter_n=chapter.number,
        book_id=chapter.book_id,
        timestamp=datetime.now(timezone.utc),
        checks=[
            QACheckResult(
                name="file_exists",
                status="pass",
                message="File exists.",
                value=100.0,
            ),
        ],
        overall_status=overall_status,
        chapter_report={
            "overall_grade": qa_grade,
            "ready_for_export": ready_for_export,
            "warnings": [] if qa_grade in {"A", "B"} else ["Needs review"],
            "failures": [] if qa_grade in {"A", "B"} else ["Critical issue"],
        },
    )
    record = persist_qa_result(test_db, chapter, qa_result)
    if manual_notes is not None:
        record.manual_notes = manual_notes
    test_db.commit()
    test_db.refresh(record)
    return record


def test_manuscript_validation_detects_mojibake() -> None:
    report = ManuscriptValidator.validate(
        42,
        [{"book_title": "Broken Book", "number": 1, "text": "This â€™ text is broken."}],
    )

    assert report.ready_for_generation is False
    assert any(issue.severity == "error" for issue in report.issues)


def test_manuscript_validation_flags_proper_nouns() -> None:
    text = " ".join(["Hermione", "Versailles", "Alexandria", "Quentin"] * 40)
    report = ManuscriptValidator.validate(
        5,
        [{"book_title": "Names Book", "number": 1, "text": text}],
    )

    assert any("proper noun density" in issue.description for issue in report.issues)


def test_manuscript_validation_detects_poetry() -> None:
    verse = "\n".join(["Wind in the trees"] * 14)
    report = ManuscriptValidator.validate(
        9,
        [{"book_title": "Verse Book", "number": 3, "text": verse}],
    )

    assert any("poetry/verse" in issue.description for issue in report.issues)


def test_difficulty_score_calculation() -> None:
    simple = ManuscriptValidator.validate(
        1,
        [{"book_title": "Simple", "number": 1, "text": "A straightforward chapter with basic prose." * 5}],
    )
    complex_report = ManuscriptValidator.validate(
        2,
        [{
            "book_title": "Complex",
            "number": 1,
            "text": ("Hermione Versailles Alexandria Quentin\n" * 16) + ("\n".join(["Short poetic line"] * 14)),
        }],
    )

    assert complex_report.difficulty_score > simple.difficulty_score


def test_quality_trend_stable(test_db: Session) -> None:
    for index in range(4):
        book = _create_book(test_db, title=f"Stable {index}")
        test_db.add(
            QualitySnapshot(
                book_id=book.id,
                completed_at=datetime.now(timezone.utc) - timedelta(hours=index),
                gate1_pass_rate=97.0 + index * 0.1,
                gate2_avg_grade=3.3 + (index * 0.05),
                gate3_overall_grade="A" if index % 2 == 0 else "B",
                chunks_regenerated=1,
                avg_wer=0.05,
                avg_lufs=-20.0,
                generation_rtf=1.4,
                issues_found=2,
            )
        )
    test_db.commit()

    trend = QualityTracker.get_quality_trend(last_n_books=10, db_session=test_db)

    assert trend["trend"] == "stable"
    assert trend["books_analyzed"] == 4


def test_quality_trend_degrading(test_db: Session) -> None:
    metrics = [
        (98.0, 3.7, "A", 0),
        (96.0, 3.2, "B", 1),
        (93.0, 2.8, "C", 3),
        (90.0, 2.4, "F", 5),
    ]
    for index, (gate1, gate2, gate3, regens) in enumerate(metrics):
        book = _create_book(test_db, title=f"Decline {index}")
        test_db.add(
            QualitySnapshot(
                book_id=book.id,
                completed_at=datetime.now(timezone.utc) - timedelta(hours=4 - index),
                gate1_pass_rate=gate1,
                gate2_avg_grade=gate2,
                gate3_overall_grade=gate3,
                chunks_regenerated=regens,
                avg_wer=0.08,
                avg_lufs=-20.0,
                generation_rtf=1.8,
                issues_found=8,
            )
        )
    test_db.commit()

    trend = QualityTracker.get_quality_trend(last_n_books=10, db_session=test_db)

    assert trend["trend"] == "degrading"
    assert trend["alerts"]


def test_export_checklist_blocks_on_failure(client, test_db: Session, monkeypatch) -> None:
    book = _create_book(test_db, title="ACX Failure")
    chapter = _create_chapter(
        test_db,
        book_id=book.id,
        number=1,
        title="One",
        generation_metadata={"gate1": {"chunks_total": 2, "chunks_pass_first_attempt": 2}},
    )
    _store_qa_record(test_db, chapter, qa_grade="A", ready_for_export=True)

    monkeypatch.setattr(
        overseer_routes,
        "_safe_book_report",
        lambda book_id, db: {
            "overall_grade": "A",
            "ready_for_export": False,
            "recommendations": [],
            "cross_chapter_checks": {
                "acx_compliance": {
                    "message": "Chapter 7 peak exceeds -3dB",
                    "status": "fail",
                    "violations": [{"chapter_n": 7}],
                },
            },
        },
    )

    response = client.get(f"/api/overseer/book/{book.id}/export-verification")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ready_for_export"] is False
    assert any(check["name"] == "acx_compliance" and check["passed"] is False for check in payload["checks"])


def test_export_checklist_passes(client, test_db: Session, monkeypatch) -> None:
    book = _create_book(test_db, title="Ready Book")
    chapter = _create_chapter(
        test_db,
        book_id=book.id,
        number=1,
        title="One",
        generation_metadata={"gate1": {"chunks_total": 2, "chunks_pass_first_attempt": 2}},
    )
    _store_qa_record(test_db, chapter, qa_grade="A", ready_for_export=True)

    monkeypatch.setattr(
        overseer_routes,
        "_safe_book_report",
        lambda book_id, db: {
            "overall_grade": "A",
            "ready_for_export": True,
            "recommendations": [],
            "cross_chapter_checks": {
                "acx_compliance": {
                    "message": "All chapters satisfy ACX/Audible requirements.",
                    "status": "pass",
                    "violations": [],
                },
            },
        },
    )

    response = client.get(f"/api/overseer/book/{book.id}/export-verification")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ready_for_export"] is True
    assert all(check["passed"] is True for check in payload["checks"])


def test_overseer_api_book_report(client, test_db: Session, monkeypatch) -> None:
    book = _create_book(test_db, title="Report Book")
    chapter = _create_chapter(
        test_db,
        book_id=book.id,
        number=1,
        title="One",
        text="Hermione crossed Versailles with deliberate grace.",
        generation_metadata={"gate1": {"chunks_total": 3, "chunks_pass_first_attempt": 2, "chunks_regenerated": 1, "avg_wer": 0.04}},
    )
    _store_qa_record(test_db, chapter, qa_grade="B", ready_for_export=True)

    monkeypatch.setattr(
        overseer_routes,
        "_safe_book_report",
        lambda book_id, db: {
            "overall_grade": "B",
            "ready_for_export": True,
            "recommendations": ["Review pronunciation for Hermione."],
            "cross_chapter_checks": {
                "acx_compliance": {
                    "message": "All chapters satisfy ACX/Audible requirements.",
                    "status": "pass",
                    "violations": [],
                },
            },
        },
    )
    monkeypatch.setattr(
        QualityTracker,
        "ensure_book_quality_snapshot",
        classmethod(
            lambda cls, book_id, db: BookQualitySnapshot(
                book_id=book_id,
                title="Report Book",
                completed_at=datetime.now(timezone.utc),
                total_chapters=1,
                gate1_pass_rate=96.0,
                gate2_avg_grade=3.0,
                gate3_overall_grade="B",
                chunks_regenerated=1,
                avg_wer=0.04,
                avg_lufs=-20.0,
                generation_rtf=1.5,
                issues_found=2,
            )
        ),
    )

    response = client.get(f"/api/overseer/book/{book.id}/report")

    assert response.status_code == 200
    payload = response.json()
    assert payload["book_id"] == book.id
    assert "manuscript_validation" in payload
    assert "gate1_summary" in payload
    assert "gate2_summary" in payload
    assert "gate3_report" in payload
    assert "export_verification" in payload


def test_overseer_api_flagged_chapters(client, test_db: Session) -> None:
    book = _create_book(test_db, title="Flagged Book")
    clean = _create_chapter(test_db, book_id=book.id, number=1, title="Clean")
    flagged = _create_chapter(test_db, book_id=book.id, number=2, title="Flagged")
    _store_qa_record(test_db, clean, qa_grade="B", ready_for_export=True)
    _store_qa_record(test_db, flagged, qa_grade="F", ready_for_export=False, manual_notes="Regenerate this chapter.")

    response = client.get(f"/api/overseer/book/{book.id}/flagged-chapters")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["chapter_n"] == 2
    assert payload[0]["qa_grade"] == "F"
