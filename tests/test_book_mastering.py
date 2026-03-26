"""Tests for the whole-book mastering pipeline."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydub import AudioSegment
from pydub.generators import Sine
from sqlalchemy.orm import Session

from src.config import settings
from src.database import Book, BookStatus, Chapter, ChapterStatus, ChapterType
from src.pipeline.book_mastering import BookMasteringPipeline
from src.pipeline.book_qa import BookQAReport, _leading_silence_ms, _trailing_silence_ms
from src.pipeline.qa_checker import QAResult

FRAME_RATE = 44100


@pytest.fixture(autouse=True)
def mastering_outputs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Isolate mastering output into a test-only directory."""

    monkeypatch.setattr(settings, "OUTPUTS_PATH", str(tmp_path / "outputs"))


@pytest.fixture(autouse=True)
def mastering_verification_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the expensive post-master verification steps."""

    async def fake_run_qa_checks_for_chapter(chapter: Chapter) -> QAResult:
        return QAResult(
            chapter_n=chapter.number,
            book_id=chapter.book_id,
            timestamp=chapter.updated_at,
            checks=[],
            overall_status="pass",
            chapter_report={"overall_grade": "A", "ready_for_export": True},
        )

    monkeypatch.setattr("src.pipeline.book_mastering.run_qa_checks_for_chapter", fake_run_qa_checks_for_chapter)
    monkeypatch.setattr(
        "src.pipeline.book_mastering.run_book_qa",
        lambda book_id, db_session: BookQAReport(
            book_id=book_id,
            title="Mastered Book",
            total_chapters=1,
            chapters_grade_a=1,
            chapters_grade_b=0,
            chapters_grade_c=0,
            chapters_grade_f=0,
            overall_grade="A",
            ready_for_export=True,
            cross_chapter_checks={},
            recommendations=[],
            export_blockers=[],
        ),
    )


def _create_book(test_db: Session, *, title: str = "Mastering Test") -> Book:
    """Create a generated book row."""

    book = Book(
        title=title,
        author="Mastering Author",
        folder_path=title.lower().replace(" ", "-"),
        status=BookStatus.GENERATED,
    )
    test_db.add(book)
    test_db.commit()
    test_db.refresh(book)
    return book


def _tone(duration_ms: int, *, gain_db: float = -18.0) -> AudioSegment:
    """Create one mono tone fixture."""

    return (
        Sine(220)
        .to_audio_segment(duration=duration_ms)
        .apply_gain(gain_db)
        .set_frame_rate(FRAME_RATE)
        .set_channels(1)
    )


def _write_audio(path: Path, audio: AudioSegment) -> None:
    """Export one WAV fixture."""

    path.parent.mkdir(parents=True, exist_ok=True)
    audio.export(path, format="wav")


def _create_chapter(test_db: Session, *, book: Book, number: int, audio: AudioSegment) -> Chapter:
    """Persist one generated chapter with a WAV file."""

    relative_audio_path = f"{book.id}-{book.title.lower().replace(' ', '-')}/chapters/{number:02d}-chapter.wav"
    absolute_audio_path = Path(settings.OUTPUTS_PATH) / relative_audio_path
    _write_audio(absolute_audio_path, audio)

    chapter = Chapter(
        book_id=book.id,
        number=number,
        title=f"Chapter {number}",
        type=ChapterType.CHAPTER,
        text_content="Synthetic mastering text.",
        word_count=4,
        status=ChapterStatus.GENERATED,
        audio_path=relative_audio_path,
        duration_seconds=len(audio) / 1000.0,
        audio_file_size_bytes=absolute_audio_path.stat().st_size,
    )
    test_db.add(chapter)
    test_db.commit()
    test_db.refresh(chapter)
    return chapter


def test_master_book_adjusts_gain(test_db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """Quiet chapters should be boosted during mastering."""

    book = _create_book(test_db, title="Adjust Gain")
    chapter = _create_chapter(
        test_db,
        book=book,
        number=1,
        audio=AudioSegment.silent(duration=750, frame_rate=FRAME_RATE) + _tone(3000, gain_db=-30.0) + AudioSegment.silent(duration=1500, frame_rate=FRAME_RATE),
    )
    audio_path = Path(settings.OUTPUTS_PATH) / chapter.audio_path
    before_peak = AudioSegment.from_file(audio_path).max_dBFS
    monkeypatch.setattr("src.pipeline.book_mastering.measure_integrated_lufs", lambda *_args, **_kwargs: -25.0)

    report = BookMasteringPipeline().master_book_sync(book.id, test_db)

    after_peak = AudioSegment.from_file(audio_path).max_dBFS
    assert report.loudness_adjustments
    assert after_peak > before_peak


def test_master_book_trims_silence(test_db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """Excess chapter-edge silence should be normalized to the target durations."""

    book = _create_book(test_db, title="Trim Silence")
    chapter = _create_chapter(
        test_db,
        book=book,
        number=1,
        audio=AudioSegment.silent(duration=2200, frame_rate=FRAME_RATE) + _tone(3000) + AudioSegment.silent(duration=4200, frame_rate=FRAME_RATE),
    )
    audio_path = Path(settings.OUTPUTS_PATH) / chapter.audio_path
    monkeypatch.setattr("src.pipeline.book_mastering.measure_integrated_lufs", lambda *_args, **_kwargs: -20.0)

    BookMasteringPipeline().master_book_sync(book.id, test_db)

    mastered = AudioSegment.from_file(audio_path)
    assert abs(_leading_silence_ms(mastered) - BookMasteringPipeline.TARGET_LEAD_IN_MS) <= 20
    assert abs(_trailing_silence_ms(mastered) - BookMasteringPipeline.TARGET_TRAIL_OUT_MS) <= 20


def test_master_book_resamples_to_acx_sample_rate(test_db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mastering should upsample native 22.05kHz chapter audio to 44.1kHz."""

    book = _create_book(test_db, title="Resample Sample Rate")
    chapter = _create_chapter(
        test_db,
        book=book,
        number=1,
        audio=(
            AudioSegment.silent(duration=750, frame_rate=22050)
            + _tone(3000, gain_db=-18.0).set_frame_rate(22050)
            + AudioSegment.silent(duration=1500, frame_rate=22050)
        ),
    )
    audio_path = Path(settings.OUTPUTS_PATH) / chapter.audio_path
    monkeypatch.setattr("src.pipeline.book_mastering.measure_integrated_lufs", lambda *_args, **_kwargs: -20.0)

    report = BookMasteringPipeline().master_book_sync(book.id, test_db)

    mastered = AudioSegment.from_file(audio_path)
    assert mastered.frame_rate == FRAME_RATE
    assert report.notes[0] == f"Resampled 1 chapters to {FRAME_RATE} Hz."


def test_master_book_peak_limits(test_db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """Hot peaks should be reduced to the safety ceiling."""

    book = _create_book(test_db, title="Peak Limit")
    chapter = _create_chapter(
        test_db,
        book=book,
        number=1,
        audio=AudioSegment.silent(duration=750, frame_rate=FRAME_RATE) + _tone(3000, gain_db=-0.1) + AudioSegment.silent(duration=1500, frame_rate=FRAME_RATE),
    )
    audio_path = Path(settings.OUTPUTS_PATH) / chapter.audio_path
    monkeypatch.setattr("src.pipeline.book_mastering.measure_integrated_lufs", lambda *_args, **_kwargs: -20.0)

    report = BookMasteringPipeline().master_book_sync(book.id, test_db)

    mastered = AudioSegment.from_file(audio_path)
    assert mastered.max_dBFS <= -1.4
    assert report.peak_limited_chapters in ([], [1])


def test_master_book_fast_chain_completes_and_resamples(test_db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """The ffmpeg fast mastering path should finish quickly and emit ACX-ready WAVs."""

    book = _create_book(test_db, title="Fast Chain Mastering")
    chapter = _create_chapter(
        test_db,
        book=book,
        number=1,
        audio=(
            AudioSegment.silent(duration=900, frame_rate=22050)
            + _tone(2500, gain_db=-22.0).set_frame_rate(22050)
            + AudioSegment.silent(duration=1800, frame_rate=22050)
        ),
    )
    audio_path = Path(settings.OUTPUTS_PATH) / chapter.audio_path
    monkeypatch.setattr("src.pipeline.book_mastering.measure_integrated_lufs", lambda *_args, **_kwargs: -20.0)

    report = BookMasteringPipeline().master_book_sync(book.id, test_db, prefer_fast_chain=True)

    mastered = AudioSegment.from_file(audio_path)
    assert mastered.frame_rate == FRAME_RATE
    assert mastered.channels == 1
    assert mastered.sample_width == 2
    assert report.notes[0] == "Using fast ffmpeg mastering chain for export-scale audio."
    assert "Resampled 1 chapters" in " ".join(report.notes)
    assert report.peak_limited_chapters == [1]


def test_master_book_preserves_good_audio(test_db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """Chapters already inside spec should be left untouched."""

    book = _create_book(test_db, title="Preserve Good")
    chapter = _create_chapter(
        test_db,
        book=book,
        number=1,
        audio=AudioSegment.silent(duration=750, frame_rate=FRAME_RATE) + _tone(3000, gain_db=-18.0) + AudioSegment.silent(duration=1500, frame_rate=FRAME_RATE),
    )
    audio_path = Path(settings.OUTPUTS_PATH) / chapter.audio_path
    monkeypatch.setattr("src.pipeline.book_mastering.measure_integrated_lufs", lambda *_args, **_kwargs: -20.1)

    report = BookMasteringPipeline().master_book_sync(book.id, test_db)

    mastered = AudioSegment.from_file(audio_path)
    assert report.loudness_adjustments == []
    assert report.edge_normalized_chapters == []
    assert report.peak_limited_chapters in ([], [1])
    assert abs(_leading_silence_ms(mastered) - BookMasteringPipeline.TARGET_LEAD_IN_MS) <= 20
    assert abs(_trailing_silence_ms(mastered) - BookMasteringPipeline.TARGET_TRAIL_OUT_MS) <= 20
