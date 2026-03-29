"""Tests for prompt 24 per-chunk quality-gate behavior."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydub import AudioSegment
from pydub.generators import Sine
from sqlalchemy.orm import Session

from src.config import ChunkValidationSettings, settings
from src.database import Book, Chapter, ChapterStatus, ChapterType
from src.pipeline.chunk_validator import (
    ChunkValidationReport,
    ChunkValidator,
    ValidationResult,
    ValidationSeverity,
    _TranscriptionOutcome,
)
from src.pipeline.generator import AudiobookGenerator


def _tone(duration_ms: int, *, gain_db: float = -6.0, frame_rate: int = 22050) -> AudioSegment:
    """Create a deterministic mono tone for audio-analysis tests."""

    return (
        Sine(220)
        .to_audio_segment(duration=duration_ms, volume=gain_db)
        .set_frame_rate(frame_rate)
        .set_channels(1)
    )


def _validator(**overrides) -> ChunkValidator:
    """Return a validator with test-local settings overrides."""

    payload = ChunkValidationSettings().model_dump()
    payload.update(overrides)
    return ChunkValidator(ChunkValidationSettings(**payload))


def _report(
    severity: ValidationSeverity,
    *,
    check: str = "text_alignment",
    message: str = "validation outcome",
) -> ChunkValidationReport:
    """Build a minimal validation report for generator retry tests."""

    return ChunkValidationReport(
        chunk_index=1,
        text="Retry me once.",
        duration_ms=900,
        results=[ValidationResult(check=check, severity=severity, message=message)],
    )


def _create_book(test_db: Session, title: str = "Quality Gate Book") -> Book:
    """Persist a minimal parsed book fixture."""

    book = Book(
        title=title,
        author="Test Author",
        folder_path=title.lower().replace(" ", "-"),
    )
    test_db.add(book)
    test_db.commit()
    test_db.refresh(book)
    return book


def _create_chapter(test_db: Session, book_id: int, text: str) -> Chapter:
    """Persist a minimal pending chapter fixture."""

    chapter = Chapter(
        book_id=book_id,
        number=1,
        title="Chapter One",
        type=ChapterType.CHAPTER,
        text_content=text,
        word_count=len(text.split()),
        status=ChapterStatus.PENDING,
    )
    test_db.add(chapter)
    test_db.commit()
    test_db.refresh(chapter)
    return chapter


class CountingEngine:
    """Small engine stub that counts generate calls."""

    def __init__(self) -> None:
        self.calls = 0
        self.loaded = False
        self.max_chunk_chars = 500
        self.sample_rate = 22050

    def load(self) -> None:
        self.loaded = True

    def unload(self) -> None:
        self.loaded = False

    def generate(self, text: str, voice: str, emotion: str | None = None, speed: float = 1.0) -> AudioSegment:
        del text, voice, emotion, speed
        self.calls += 1
        return _tone(900)


def test_validate_chunk_passes_valid_audio() -> None:
    """Balanced narration audio should pass the baseline chunk sanity gate."""

    generator = AudiobookGenerator(CountingEngine())

    valid, reason = generator._validate_chunk(
        _tone(1200, gain_db=-6.0),
        "This chunk contains enough words to look like valid narration output.",
    )

    assert valid is True
    assert reason == "OK"


def test_validate_chunk_rejects_silence() -> None:
    """Silent chunks should fail before stitching."""

    generator = AudiobookGenerator(CountingEngine())

    valid, reason = generator._validate_chunk(
        AudioSegment.silent(duration=500, frame_rate=22050),
        "This should not be silent audio.",
    )

    assert valid is False
    assert "Silent chunk" in reason


def test_validate_chunk_rejects_clipping() -> None:
    """Near-0 dBFS chunks should be rejected before stitching."""

    generator = AudiobookGenerator(CountingEngine())

    valid, reason = generator._validate_chunk(
        _tone(900, gain_db=0.0),
        "This chunk should fail because the peak is clipped.",
    )

    assert valid is False
    assert "Clipping detected" in reason


def test_validate_chunk_rejects_too_short_audio() -> None:
    """Sub-100ms chunks for real text should fail fast."""

    generator = AudiobookGenerator(CountingEngine())

    valid, reason = generator._validate_chunk(
        _tone(50, gain_db=-6.0),
        "This chunk is intentionally far too short for the amount of text provided.",
    )

    assert valid is False
    assert "Too short" in reason


def test_validate_chunk_rejects_hallucination_duration() -> None:
    """Implausibly slow speech should be treated as a looping hallucination."""

    generator = AudiobookGenerator(CountingEngine())

    valid, reason = generator._validate_chunk(
        _tone(25_000, gain_db=-6.0),
        "one two three four five six seven eight nine ten",
    )

    assert valid is False
    assert "Suspected hallucination" in reason


def test_text_alignment_pass() -> None:
    """Matching transcript text should pass STT alignment."""

    validator = _validator()
    result = validator.check_text_alignment(
        _tone(1000),
        "The quick brown fox jumps over the lazy dog.",
        transcription=_TranscriptionOutcome(transcript="the quick brown fox jumps over the lazy dog"),
    )

    assert result.severity == ValidationSeverity.PASS
    assert result.details is not None
    assert result.details["wer"] < 0.10


def test_text_alignment_fail() -> None:
    """Mismatched transcript text should fail STT alignment."""

    validator = _validator()
    result = validator.check_text_alignment(
        _tone(1000),
        "The quick brown fox jumps over the lazy dog.",
        transcription=_TranscriptionOutcome(transcript="completely unrelated words appear here"),
    )

    assert result.severity == ValidationSeverity.FAIL
    assert result.details is not None
    assert result.details["wer"] > 0.30


def test_repeat_detection_finds_repeated_phrase() -> None:
    """Repeated 3-word phrases should fail validation."""

    result = _validator().check_repeats(
        _tone(1200),
        "the cat sat the cat sat",
        transcript="the cat sat the cat sat",
    )

    assert result.severity == ValidationSeverity.FAIL
    assert "the cat sat" in result.message


def test_repeat_detection_allows_intentional() -> None:
    """A simple intentional repeat like 'very, very' should not fail."""

    result = _validator().check_repeats(
        _tone(800),
        "very, very",
        transcript="very very",
    )

    assert result.severity == ValidationSeverity.PASS


def test_gibberish_detection_clean_audio() -> None:
    """Stable clean audio should pass clarity checks."""

    result = _validator().check_audio_clarity(_tone(2000))

    assert result.severity == ValidationSeverity.PASS


def test_duration_detailed_within_range() -> None:
    """Reasonable chunk timing should pass duration validation."""

    result = _validator().check_duration_detailed(
        _tone(4500),
        "This narration sample has enough words to land inside the expected duration range.",
    )

    assert result.severity == ValidationSeverity.PASS


def test_duration_detailed_too_long() -> None:
    """Very long chunks for short text should fail duration validation."""

    result = _validator().check_duration_detailed(
        _tone(15000),
        "This chunk should not take fifteen seconds to narrate.",
    )

    assert result.severity == ValidationSeverity.FAIL


@pytest.mark.asyncio
async def test_regeneration_on_fail(test_db: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Chunks should retry when validation returns FAIL and succeed once validation passes."""

    monkeypatch.setattr(settings, "OUTPUTS_PATH", str(tmp_path / "outputs"))

    engine = CountingEngine()
    generator = AudiobookGenerator(engine)
    book = _create_book(test_db)
    chapter = _create_chapter(
        test_db,
        book.id,
        "This chunk should retry until the validator is satisfied.",
    )

    reports = iter(
        [
            _report(ValidationSeverity.FAIL, message="bad transcript"),
            _report(ValidationSeverity.FAIL, message="bad transcript again"),
            _report(ValidationSeverity.PASS, check="duration", message="all clear"),
        ]
    )

    monkeypatch.setattr(generator.chunk_validator, "validate", lambda *args, **kwargs: next(reports))

    duration = await generator.generate_chapter(book.id, chapter, test_db)

    test_db.refresh(chapter)
    assert duration > 0
    assert chapter.status == ChapterStatus.GENERATED
    assert engine.calls == 3


def test_graceful_whisper_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing mlx-whisper should return a non-fatal INFO result."""

    def _raise_missing(cls, model_name: str):
        del cls, model_name
        raise ImportError("mlx-whisper missing")

    monkeypatch.setattr(ChunkValidator, "_load_whisper_model", classmethod(_raise_missing))
    result = _validator().check_text_alignment(_tone(1000), "Hello world")

    assert result.severity == ValidationSeverity.INFO
    assert result.message == "STT alignment check skipped (mlx-whisper not installed)"


def test_validation_report_worst_severity() -> None:
    """Report severity should reflect the worst contained result."""

    report = ChunkValidationReport(
        chunk_index=1,
        text="Hello world",
        duration_ms=1000,
        results=[
            ValidationResult("duration", ValidationSeverity.PASS, "ok"),
            ValidationResult("repeat_detection", ValidationSeverity.WARNING, "warning"),
            ValidationResult("text_alignment", ValidationSeverity.FAIL, "fail"),
        ],
    )

    assert report.worst_severity == ValidationSeverity.FAIL
    assert report.needs_regeneration is True
