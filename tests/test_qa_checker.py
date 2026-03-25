"""Unit tests for automated audio QA checks."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from pydub import AudioSegment
from pydub.generators import Sine
from sqlalchemy.orm import Session

from src.database import Book, BookStatus, Chapter, ChapterStatus, ChapterType
from src.pipeline.qa_checker import (
    check_clipping,
    check_duration,
    check_file_exists,
    check_silence_gaps,
    check_volume_consistency,
    run_qa_checks,
)

FRAME_RATE = 22050


def _write_audio(path: Path, audio: AudioSegment) -> Path:
    """Export a WAV fixture and return its path."""

    audio.export(path, format="wav")
    return path


def _tone(duration_ms: int, *, gain_db: float = -18.0) -> AudioSegment:
    """Return a mono sine wave segment."""

    return Sine(220).to_audio_segment(duration=duration_ms).apply_gain(gain_db).set_channels(1)


def _constant_wave(duration_ms: int, amplitude: float) -> AudioSegment:
    """Return a mono constant-amplitude waveform."""

    sample_count = int(FRAME_RATE * (duration_ms / 1000))
    samples = np.full(sample_count, int(np.iinfo(np.int16).max * amplitude), dtype=np.int16)
    return AudioSegment(
        data=samples.tobytes(),
        sample_width=2,
        frame_rate=FRAME_RATE,
        channels=1,
    )


def _create_book(test_db: Session) -> Book:
    """Create a parsed book for QA tests."""

    book = Book(
        title="QA Test Book",
        author="Test Author",
        folder_path="qa-test-book",
        status=BookStatus.PARSED,
    )
    test_db.add(book)
    test_db.commit()
    test_db.refresh(book)
    return book


def _create_chapter(test_db: Session, book_id: int, audio_path: str) -> Chapter:
    """Create a generated chapter row for QA integration tests."""

    chapter = Chapter(
        book_id=book_id,
        number=1,
        title="QA Chapter",
        type=ChapterType.CHAPTER,
        text_content="one two three four five six seven eight nine ten",
        word_count=10,
        status=ChapterStatus.GENERATED,
        audio_path=audio_path,
    )
    test_db.add(chapter)
    test_db.commit()
    test_db.refresh(chapter)
    return chapter


def test_check_file_exists_pass(tmp_path: Path) -> None:
    """Existing non-empty WAV files should pass the file existence check."""

    audio_path = _write_audio(tmp_path / "exists.wav", _tone(1000))

    result = check_file_exists(audio_path)

    assert result.status == "pass"
    assert "File exists" in result.message
    assert result.value > 0


def test_check_file_exists_fail(tmp_path: Path) -> None:
    """Missing files should fail the file existence check."""

    result = check_file_exists(tmp_path / "missing.wav")

    assert result.status == "fail"
    assert "does not exist" in result.message


def test_check_duration_pass(tmp_path: Path) -> None:
    """Durations within the configured tolerance should pass."""

    audio_path = _write_audio(tmp_path / "duration-pass.wav", _tone(4000))

    result = check_duration(audio_path, 10)

    assert result.status == "pass"
    assert result.value == pytest.approx(4.0, abs=0.1)


def test_check_duration_warning(tmp_path: Path) -> None:
    """Durations outside the configured tolerance should warn."""

    audio_path = _write_audio(tmp_path / "duration-warning.wav", _tone(6000))

    result = check_duration(audio_path, 10)

    assert result.status == "warning"
    assert "outside expected range" in result.message


def test_check_clipping_pass(tmp_path: Path) -> None:
    """Audio below the clipping threshold should pass."""

    audio_path = _write_audio(tmp_path / "clipping-pass.wav", _constant_wave(1000, 0.5))

    result = check_clipping(audio_path)

    assert result.status == "pass"
    assert result.value < 0.95


def test_check_clipping_fail(tmp_path: Path) -> None:
    """Audio at or above the clipping threshold should fail."""

    audio_path = _write_audio(tmp_path / "clipping-fail.wav", _constant_wave(1000, 0.98))

    result = check_clipping(audio_path)

    assert result.status == "fail"
    assert result.value >= 0.95


def test_check_silence_gaps_pass(tmp_path: Path) -> None:
    """Files without long mid-chapter silence should pass."""

    audio = _tone(2500) + _tone(2500)
    audio_path = _write_audio(tmp_path / "silence-pass.wav", audio)

    result = check_silence_gaps(audio_path)

    assert result.status == "pass"
    assert result.value == 0


def test_check_silence_gaps_warning(tmp_path: Path) -> None:
    """Mid-chapter silence between three and five seconds should warn."""

    audio = _tone(2000) + AudioSegment.silent(duration=4000) + _tone(4000)
    audio_path = _write_audio(tmp_path / "silence-warning.wav", audio)

    result = check_silence_gaps(audio_path)

    assert result.status == "warning"
    assert result.value == pytest.approx(4.0, abs=0.1)


def test_check_silence_gaps_fail(tmp_path: Path) -> None:
    """Mid-chapter silence beyond five seconds should fail."""

    audio = _tone(2000) + AudioSegment.silent(duration=6000) + _tone(4000)
    audio_path = _write_audio(tmp_path / "silence-fail.wav", audio)

    result = check_silence_gaps(audio_path)

    assert result.status == "fail"
    assert result.value == pytest.approx(6.0, abs=0.1)


def test_check_volume_consistency_pass(tmp_path: Path) -> None:
    """Uniform chunk loudness should pass the consistency check."""

    audio = _tone(1000) + _tone(1000) + _tone(1000) + _tone(1000)
    audio_path = _write_audio(tmp_path / "volume-pass.wav", audio)

    result = check_volume_consistency(audio_path)

    assert result.status == "pass"
    assert result.value <= 3.0


def test_check_volume_consistency_warning(tmp_path: Path) -> None:
    """Large chunk-to-chunk loudness swings should warn."""

    audio = _tone(1000, gain_db=-9.0) + _tone(1000, gain_db=-30.0) + _tone(1000, gain_db=-12.0)
    audio_path = _write_audio(tmp_path / "volume-warning.wav", audio)

    result = check_volume_consistency(audio_path)

    assert result.status == "warning"
    assert result.value > 3.0


@pytest.mark.asyncio
async def test_run_qa_checks_handles_corrupted_audio_gracefully(test_db: Session, tmp_path: Path) -> None:
    """Corrupted WAV input should degrade to failed checks instead of raising."""

    corrupted_path = tmp_path / "corrupted.wav"
    corrupted_path.write_bytes(b"not-a-real-wave-file")

    book = _create_book(test_db)
    _create_chapter(test_db, book.id, str(corrupted_path))

    result = await run_qa_checks(book.id, 1, db_session=test_db)

    assert result.overall_status == "fail"
    assert any(check.status == "fail" for check in result.checks)
    assert any("Unable to analyze audio" in check.message for check in result.checks[1:])
