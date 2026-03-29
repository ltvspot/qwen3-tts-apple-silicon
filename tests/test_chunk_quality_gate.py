"""Tests for prompt 24 per-chunk quality-gate behavior."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from pydub import AudioSegment
from pydub.generators import Sine
from sqlalchemy.orm import Session

from src.config import ChunkValidationSettings, default_application_settings, settings
from src.database import Book, Chapter, ChapterStatus, ChapterType
from src.engines import TextChunker
from src.pipeline.chunk_validator import (
    ChunkValidationReport,
    ChunkValidator,
    ValidationResult,
    ValidationSeverity,
    _TranscriptionOutcome,
)
from src.pipeline.generator import AudiobookGenerator


def _tone(duration_ms: int, *, gain_db: float = -6.0, frame_rate: int = 24000) -> AudioSegment:
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


def _patch_app_settings(
    monkeypatch: pytest.MonkeyPatch,
    *,
    tts_output_sample_rate: int = 24000,
    stt_alignment_enabled: bool = True,
) -> None:
    """Patch generator-facing application settings without touching persisted state."""

    app_settings = default_application_settings()
    app_settings.chunk_validation = ChunkValidationSettings(stt_alignment_enabled=stt_alignment_enabled)
    app_settings.output_preferences.tts_output_sample_rate = tts_output_sample_rate
    monkeypatch.setattr("src.pipeline.generator.get_application_settings", lambda: app_settings)
    monkeypatch.setattr("src.pipeline.chunk_validator.get_application_settings", lambda: app_settings)


def _report(
    severity: ValidationSeverity,
    *,
    check: str = "text_alignment",
    message: str = "validation outcome",
    details: dict[str, float] | None = None,
) -> ChunkValidationReport:
    """Build a minimal validation report for generator retry tests."""

    return ChunkValidationReport(
        chunk_index=1,
        text="Retry me once.",
        duration_ms=900,
        results=[ValidationResult(check=check, severity=severity, message=message, details=details)],
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
        self.sample_rate = 24000

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
        AudioSegment.silent(duration=500, frame_rate=24000),
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
    _patch_app_settings(monkeypatch, tts_output_sample_rate=24000)

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


@pytest.mark.asyncio
async def test_extreme_wer_fast_fails_into_split_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Extreme first-pass WER should skip redundant retries and split immediately."""

    engine = CountingEngine()
    generator = AudiobookGenerator(engine)
    validation_text = "Alpha sentence. Beta sentence."
    full_text_calls = 0

    def fake_validate(audio, input_text, voice=None, speed=1.0, **kwargs):
        del audio, voice, speed, kwargs
        nonlocal full_text_calls
        if input_text == validation_text:
            full_text_calls += 1
            if full_text_calls == 1:
                return _report(
                    ValidationSeverity.FAIL,
                    message="extreme transcript mismatch",
                    details={"wer": 2.5},
                )
        return _report(ValidationSeverity.PASS, check="duration", message="all clear")

    monkeypatch.setattr(generator.chunk_validator, "validate", fake_validate)

    _, validation_report, failed_validation, attempts_used = await generator._generate_chunk_with_retry(
        validation_text,
        validation_text=validation_text,
        chunk_index=0,
        voice_name="Ethan",
        emotion="neutral",
        speed=1.0,
        chapter_number=1,
        book_id=1,
        should_cancel=None,
        expected_sample_rate=24000,
    )

    assert validation_report.needs_regeneration is False
    assert failed_validation is False
    assert attempts_used == 3
    assert engine.calls == 3


def test_merge_minimum_word_chunks_absorbs_short_interior_chunks() -> None:
    """Interior chunks below the minimum word count should merge into neighbors."""

    generator = AudiobookGenerator(CountingEngine())
    chunk_plans = [
        TextChunker.ChunkPlan(
            text="This opening chunk has enough words to stand on its own for stable narration output. ",
            ends_sentence=True,
            ends_paragraph=False,
        ),
        TextChunker.ChunkPlan(
            text="Too short. ",
            ends_sentence=True,
            ends_paragraph=False,
        ),
        TextChunker.ChunkPlan(
            text="This following chunk also has enough words to remain a separate narration unit. ",
            ends_sentence=True,
            ends_paragraph=False,
        ),
        TextChunker.ChunkPlan(
            text="Tail. ",
            ends_sentence=True,
            ends_paragraph=False,
        ),
    ]

    merged = generator._merge_minimum_word_chunks(chunk_plans)

    assert len(merged) == 2
    assert merged[0].text == chunk_plans[0].text + chunk_plans[1].text + chunk_plans[2].text
    assert merged[-1].text == chunk_plans[-1].text


def test_retry_speed_variation_uses_wider_offsets() -> None:
    """Retry speed changes should use the wider +/-0.05 tuning offsets."""

    generator = AudiobookGenerator(CountingEngine())

    assert generator._vary_retry_speed(1.0, 1) == pytest.approx(1.0)
    assert generator._vary_retry_speed(1.0, 2) == pytest.approx(1.05)
    assert generator._vary_retry_speed(1.0, 3) == pytest.approx(0.95)


@pytest.mark.asyncio
async def test_generate_chapter_passes_tts_output_sample_rate_to_validator(
    test_db: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Chunk validation should use the configured TTS output sample rate."""

    monkeypatch.setattr(settings, "OUTPUTS_PATH", str(tmp_path / "outputs"))
    _patch_app_settings(monkeypatch, tts_output_sample_rate=24000)

    engine = CountingEngine()
    generator = AudiobookGenerator(engine)
    book = _create_book(test_db, "Sample Rate Book")
    chapter = _create_chapter(test_db, book.id, "This chunk validates against the TTS sample rate.")
    expected_sample_rates: list[int | None] = []

    def capture_validate(*args, expected_sample_rate=None, **kwargs):
        expected_sample_rates.append(expected_sample_rate)
        return _report(ValidationSeverity.PASS, check="duration", message="all clear")

    monkeypatch.setattr(generator.chunk_validator, "validate", capture_validate)

    await generator.generate_chapter(book.id, chapter, test_db)

    assert expected_sample_rates
    assert set(expected_sample_rates) == {24000}


@pytest.mark.asyncio
async def test_retry_budget_logging_reports_chapter_efficiency(
    test_db: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Chapter generation should log retry budget usage for operators."""

    monkeypatch.setattr(settings, "OUTPUTS_PATH", str(tmp_path / "outputs"))

    generator = AudiobookGenerator(CountingEngine())
    book = _create_book(test_db, "Retry Budget Book")
    chapter = _create_chapter(test_db, book.id, "This chapter should finish in one clean chunk.")

    monkeypatch.setattr(
        generator.chunk_validator,
        "validate",
        lambda *args, **kwargs: _report(ValidationSeverity.PASS, check="duration", message="all clear"),
    )

    caplog.set_level(logging.INFO)

    await generator.generate_chapter(book.id, chapter, test_db)

    assert "Chapter 1: 1 chunks x 3 attempts = 3 max generations" in caplog.text
    assert "Chapter 1 complete: 1 generations used (67% efficient)" in caplog.text


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
