"""Integration coverage for the deep audio QA pipeline."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf
from sqlalchemy.orm import Session

from src.database import AudioQAResult, Book, BookStatus, Chapter, ChapterStatus, ChapterType
from src.pipeline.audio_qa.models import DependencyNotice
from src.pipeline.audio_qa.qa_scorer import load_book_audio_qa_report, persist_chapter_audio_qa_result, run_chapter_audio_qa
from src.pipeline.audio_qa.audio_quality_analyzer import AudioQualityAnalyzer
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
        text_content="hello world from chapter one with steady pace and natural pauses today",
        word_count=12,
        status=ChapterStatus.GENERATED,
        audio_path=audio_path,
    )
    test_db.add(chapter)
    test_db.commit()
    test_db.refresh(chapter)
    return chapter


def _write_synthetic_audio(audio_path: Path) -> None:
    """Write a short synthetic chapter clip with realistic silence and level changes."""

    sample_rate = 16000
    time_axis = np.linspace(0.0, 5.0, sample_rate * 5, endpoint=False)
    carrier = 0.06 * np.sin(2 * np.pi * 220 * time_axis)
    envelope = np.ones_like(carrier)
    envelope[: int(sample_rate * 0.5)] = 0.0
    envelope[int(sample_rate * 2.0): int(sample_rate * 2.3)] = 0.0
    envelope[-int(sample_rate * 0.6):] = 0.0
    noise = 0.002 * np.random.default_rng(42).standard_normal(carrier.shape)
    samples = (carrier * envelope) + noise
    sf.write(audio_path, samples, sample_rate)


def test_run_chapter_audio_qa_pipeline_with_synthetic_audio(tmp_path: Path, test_db: Session, monkeypatch) -> None:
    """A generated WAV should flow through transcription, timing, quality, persistence, and reload."""

    audio_path = tmp_path / "synthetic-chapter.wav"
    _write_synthetic_audio(audio_path)

    book = _create_book(test_db, title="Deep QA Integration")
    chapter = _create_generated_chapter(test_db, book_id=book.id, number=1, audio_path=str(audio_path))

    monkeypatch.setattr(TranscriptionAccuracyChecker, "_load_backend", lambda self: object())
    monkeypatch.setattr(
        TranscriptionAccuracyChecker,
        "_transcribe",
        lambda self, backend, path: {
            "text": "hello world from chapter one with steady pace and natural pauses today",
            "segments": [
                {"start": 0.5, "end": 2.0, "text": "hello world from chapter one"},
                {"start": 2.3, "end": 4.4, "text": "with steady pace and natural pauses today"},
            ],
        },
    )
    monkeypatch.setattr(
        AudioQualityAnalyzer,
        "_measure_loudness",
        lambda self, path, mono, sample_rate: (
            -20.2,
            4.0,
            DependencyNotice(dependency="pyloudnorm", available=True),
        ),
    )

    result = run_chapter_audio_qa(chapter, test_db)
    record = persist_chapter_audio_qa_result(test_db, chapter, result)
    test_db.commit()

    loaded_report = load_book_audio_qa_report(book.id, test_db)

    assert result.book_id == book.id
    assert result.chapter_id == chapter.id
    assert result.transcription.status == "pass"
    assert result.timing.actual_duration_seconds is not None
    assert result.quality.snr_db is not None
    assert result.scoring.overall > 80.0
    assert record.overall_score == result.scoring.overall
    assert loaded_report.chapter_count == 1
    assert loaded_report.chapters[0].chapter_n == chapter.number
    assert test_db.query(AudioQAResult).count() == 1
