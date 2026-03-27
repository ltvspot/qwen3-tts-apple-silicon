"""Task-1 coverage for deep audio QA scaffolding."""

from __future__ import annotations

import json

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from src.database import AudioQAResult, Book, Chapter, ChapterStatus, ChapterType
from src.pipeline.audio_qa import (
    AudioQualityAnalyzer,
    AudioQAScorer,
    TimingAndPacingAnalyzer,
    TranscriptionAccuracyChecker,
)
from src.pipeline.audio_qa.models import (
    AudioQAIssue,
    AudioQualityAnalysis,
    TimingAnalysis,
    TranscriptDiffEntry,
    TranscriptionAnalysis,
)
from src.pipeline.audio_qa.transcription_checker import compute_word_error_rate, normalize_transcript_text


def test_audio_qa_table_is_present(test_db: Session) -> None:
    """The deep audio QA table should be part of the metadata schema."""

    inspector = inspect(test_db.get_bind())

    assert "audio_qa_results" in inspector.get_table_names()


def test_audio_qa_result_crud_round_trip(test_db: Session) -> None:
    """The new audio QA result table should store a chapter report payload."""

    book = Book(
        title="Deep QA",
        author="Alexandria",
        folder_path="deep-qa",
    )
    test_db.add(book)
    test_db.flush()

    chapter = Chapter(
        book_id=book.id,
        number=1,
        title="Chapter 1",
        type=ChapterType.CHAPTER,
        text_content="Hello world from the chapter.",
        word_count=5,
        status=ChapterStatus.GENERATED,
    )
    test_db.add(chapter)
    test_db.flush()

    record = AudioQAResult(
        book_id=book.id,
        chapter_id=chapter.id,
        chapter_n=chapter.number,
        transcription_score=91.0,
        timing_score=87.0,
        quality_score=89.0,
        overall_score=89.5,
        overall_grade="B",
        overall_status="warning",
        report_json=json.dumps({"summary": "stored"}),
        issues_count=2,
    )
    test_db.add(record)
    test_db.commit()

    stored = test_db.query(AudioQAResult).one()

    assert stored.book_id == book.id
    assert stored.chapter_id == chapter.id
    assert stored.chapter_n == 1
    assert stored.overall_grade == "B"
    assert json.loads(stored.report_json) == {"summary": "stored"}


def test_audio_qa_package_exports_expected_types() -> None:
    """The Task-1 package skeleton should expose the main analyzers."""

    assert TranscriptionAccuracyChecker is not None
    assert TimingAndPacingAnalyzer is not None
    assert AudioQualityAnalyzer is not None
    assert AudioQAScorer is not None


def test_normalize_transcript_text_collapses_case_and_spacing() -> None:
    """Normalization should be stable across punctuation and whitespace."""

    assert normalize_transcript_text(" Hello,\nWORLD!! ") == "hello world"


def test_compute_word_error_rate_matches_identical_text() -> None:
    """WER should be zero for identical normalized transcripts."""

    assert compute_word_error_rate("The quick brown fox", "the quick brown fox") == 0.0


def test_compute_word_error_rate_detects_substitution() -> None:
    """WER should increase when one token changes."""

    assert compute_word_error_rate("the quick brown fox", "the quick blue fox") == 0.25


def test_models_use_safe_default_factories() -> None:
    """Mutable model fields should not be shared between instances."""

    first = TranscriptionAnalysis()
    second = TranscriptionAnalysis()
    first.diff.append(TranscriptDiffEntry(operation="replace", expected="a", actual="b"))
    first.issues.append(AudioQAIssue(code="x", category="transcription", severity="warning", message="issue"))

    assert second.diff == []
    assert second.issues == []


def test_scorer_computes_weighted_breakdown() -> None:
    """The scoring skeleton should provide a deterministic weighted result."""

    scorer = AudioQAScorer()
    scoring = scorer.score(
        TranscriptionAnalysis(score=95.0, status="completed"),
        TimingAnalysis(score=80.0, status="completed"),
        AudioQualityAnalysis(score=90.0, status="completed"),
    )

    assert scoring.overall == 90.25
    assert scoring.grade == "A"
    assert scoring.status == "pass"
