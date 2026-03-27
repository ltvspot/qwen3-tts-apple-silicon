"""API tests for the deep audio QA endpoints."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from src.database import AudioQAResult, Book, BookStatus, Chapter, ChapterStatus, ChapterType
from src.pipeline.audio_qa.models import (
    AudioQualityAnalysis,
    DependencyNotice,
    TimingAnalysis,
    TranscriptionAnalysis,
)
from src.pipeline.audio_qa.audio_quality_analyzer import AudioQualityAnalyzer
from src.pipeline.audio_qa.timing_analyzer import TimingAndPacingAnalyzer
from src.pipeline.audio_qa.transcription_checker import TranscriptionAccuracyChecker


def _create_book(test_db: Session, *, title: str) -> Book:
    book = Book(
        title=title,
        author="Alexandria",
        folder_path=title.lower().replace(" ", "-"),
        status=BookStatus.GENERATED,
    )
    test_db.add(book)
    test_db.commit()
    test_db.refresh(book)
    return book


def _create_generated_chapter(test_db: Session, *, book_id: int, number: int, audio_path: str) -> Chapter:
    chapter = Chapter(
        book_id=book_id,
        number=number,
        title=f"Chapter {number}",
        type=ChapterType.CHAPTER,
        text_content="Hello world from chapter one",
        word_count=5,
        status=ChapterStatus.GENERATED,
        audio_path=audio_path,
    )
    test_db.add(chapter)
    test_db.commit()
    test_db.refresh(chapter)
    return chapter


def _stub_transcription(self, audio_path, reference_text):  # noqa: ANN001
    del self, audio_path, reference_text
    return TranscriptionAnalysis(
        dependency=DependencyNotice(dependency="mlx-whisper", available=True),
        transcript="hello world from chapter one",
        normalized_reference="hello world from chapter one",
        normalized_transcript="hello world from chapter one",
        reference_word_count=5,
        transcript_word_count=5,
        word_error_rate=0.0,
        score=100.0,
        status="pass",
    )


def _stub_timing(self, audio_path, reference_text):  # noqa: ANN001
    del self, audio_path, reference_text
    return TimingAnalysis(
        dependency=DependencyNotice(dependency="librosa", available=True),
        estimated_duration_seconds=5.8,
        actual_duration_seconds=6.0,
        speech_rate_wpm=140.0,
        pause_ratio=0.05,
        score=96.0,
        status="pass",
    )


def _stub_quality(self, audio_path):  # noqa: ANN001
    del self, audio_path
    return AudioQualityAnalysis(
        dependency=DependencyNotice(dependency="pyloudnorm", available=True),
        integrated_lufs=-20.0,
        loudness_range_lu=4.0,
        peak_dbfs=-6.0,
        snr_db=28.0,
        clipping_ratio=0.0,
        score=94.0,
        status="pass",
    )


def test_post_chapter_deep_qa_runs_and_persists(client, test_db: Session, tmp_path: Path, monkeypatch) -> None:
    """Running deep QA for one chapter should store an AudioQAResult row."""

    monkeypatch.setattr(TranscriptionAccuracyChecker, "analyze", _stub_transcription)
    monkeypatch.setattr(TimingAndPacingAnalyzer, "analyze", _stub_timing)
    monkeypatch.setattr(AudioQualityAnalyzer, "analyze", _stub_quality)

    book = _create_book(test_db, title="Deep QA Chapter API")
    audio_path = tmp_path / "chapter.wav"
    audio_path.write_bytes(b"fake")
    chapter = _create_generated_chapter(test_db, book_id=book.id, number=1, audio_path=str(audio_path))

    response = client.post(f"/api/books/{book.id}/chapters/{chapter.id}/deep-qa")

    assert response.status_code == 200
    payload = response.json()
    assert payload["chapter_id"] == chapter.id
    assert payload["scoring"]["status"] == "pass"
    assert payload["ready_for_export"] is True

    record = test_db.query(AudioQAResult).one()
    assert record.book_id == book.id
    assert record.chapter_id == chapter.id
    assert record.overall_grade == "A"


def test_post_book_deep_qa_returns_aggregate_report(client, test_db: Session, tmp_path: Path, monkeypatch) -> None:
    """Book-level deep QA should aggregate per-chapter reports."""

    monkeypatch.setattr(TranscriptionAccuracyChecker, "analyze", _stub_transcription)
    monkeypatch.setattr(TimingAndPacingAnalyzer, "analyze", _stub_timing)
    monkeypatch.setattr(AudioQualityAnalyzer, "analyze", _stub_quality)

    book = _create_book(test_db, title="Deep QA Book API")
    for number in (1, 2):
        audio_path = tmp_path / f"chapter-{number}.wav"
        audio_path.write_bytes(b"fake")
        _create_generated_chapter(test_db, book_id=book.id, number=number, audio_path=str(audio_path))

    response = client.post(f"/api/books/{book.id}/deep-qa")

    assert response.status_code == 200
    payload = response.json()
    assert payload["book_id"] == book.id
    assert payload["chapter_count"] == 2
    assert payload["average_score"] == 97.1
    assert payload["grade_counts"]["A"] == 2
    assert payload["ready_for_export"] is True


def test_get_book_deep_qa_report_reads_stored_results(client, test_db: Session, tmp_path: Path, monkeypatch) -> None:
    """The GET qa-report endpoint should return the persisted aggregate."""

    monkeypatch.setattr(TranscriptionAccuracyChecker, "analyze", _stub_transcription)
    monkeypatch.setattr(TimingAndPacingAnalyzer, "analyze", _stub_timing)
    monkeypatch.setattr(AudioQualityAnalyzer, "analyze", _stub_quality)

    book = _create_book(test_db, title="Deep QA Report API")
    audio_path = tmp_path / "chapter.wav"
    audio_path.write_bytes(b"fake")
    chapter = _create_generated_chapter(test_db, book_id=book.id, number=1, audio_path=str(audio_path))

    post_response = client.post(f"/api/books/{book.id}/chapters/{chapter.id}/deep-qa")
    assert post_response.status_code == 200

    response = client.get(f"/api/books/{book.id}/qa-report")

    assert response.status_code == 200
    payload = response.json()
    assert payload["chapter_count"] == 1
    assert payload["chapters"][0]["chapter_id"] == chapter.id
    assert payload["chapters"][0]["scoring"]["overall"] == 97.1


def test_get_book_deep_qa_report_404s_without_results(client, test_db: Session) -> None:
    """Books without stored deep-QA results should return 404."""

    book = _create_book(test_db, title="Deep QA Missing Report")

    response = client.get(f"/api/books/{book.id}/qa-report")

    assert response.status_code == 404
    assert "No deep audio QA results" in response.json()["detail"]
