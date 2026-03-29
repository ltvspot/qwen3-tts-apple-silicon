"""Tests for the Gate 3 whole-book quality checks."""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest
from pydub import AudioSegment
from pydub.generators import Sine
from sqlalchemy.orm import Session

from src.config import settings
from src.database import (
    Book,
    BookStatus,
    Chapter,
    ChapterQARecord,
    ChapterStatus,
    ChapterType,
    QAAutomaticStatus,
)
from src.pipeline.book_qa import (
    check_acx_compliance,
    check_chapter_transitions,
    check_cross_chapter_loudness,
    check_loudness_range,
    check_cross_chapter_pacing,
    check_cross_chapter_voice,
    build_export_readiness_summary,
    ACX_REQUIREMENTS,
    run_book_qa,
)

FRAME_RATE = 44100


@pytest.fixture(autouse=True)
def book_quality_outputs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Route test audio into a private outputs directory."""

    monkeypatch.setattr(settings, "OUTPUTS_PATH", str(tmp_path / "outputs"))


def _create_book(test_db: Session, *, title: str = "Book QA Test") -> Book:
    """Create a generated book."""

    book = Book(
        title=title,
        author="Test Author",
        folder_path=title.lower().replace(" ", "-"),
        status=BookStatus.GENERATED,
    )
    test_db.add(book)
    test_db.commit()
    test_db.refresh(book)
    return book


def _tone(duration_ms: int, frequency: int = 220, *, gain_db: float = -18.0) -> AudioSegment:
    """Create a deterministic mono sine-wave chapter."""

    return (
        Sine(frequency)
        .to_audio_segment(duration=duration_ms)
        .apply_gain(gain_db)
        .set_frame_rate(FRAME_RATE)
        .set_channels(1)
    )


def _with_edges(audio: AudioSegment, *, lead_ms: int = 750, trail_ms: int = 1500) -> AudioSegment:
    """Wrap spoken audio in ACX-friendly room tone."""

    def _room_tone(duration_ms: int) -> AudioSegment:
        sample_count = max(int(FRAME_RATE * (duration_ms / 1000.0)), 1)
        rng = np.random.default_rng(53 + duration_ms)
        samples = rng.standard_normal(sample_count).astype(np.float32)
        samples /= max(float(np.max(np.abs(samples))), 1.0)
        samples *= float(10 ** (-65.0 / 20.0))
        int_samples = np.round(samples * np.iinfo(np.int16).max).astype(np.int16)
        return AudioSegment(
            data=int_samples.tobytes(),
            sample_width=2,
            frame_rate=FRAME_RATE,
            channels=1,
        )

    return (
        _room_tone(lead_ms)
        + audio
        + _room_tone(trail_ms)
    )


def _write_chapter_audio(path: Path, audio: AudioSegment) -> None:
    """Export one WAV fixture."""

    path.parent.mkdir(parents=True, exist_ok=True)
    audio.export(path, format="wav")


def _create_chapter(
    test_db: Session,
    *,
    book: Book,
    number: int,
    title: str,
    audio: AudioSegment,
    word_count: int = 120,
    chapter_type: ChapterType = ChapterType.CHAPTER,
) -> Chapter:
    """Persist one generated chapter row plus a WAV file."""

    relative_audio_path = f"{book.id}-{book.title.lower().replace(' ', '-')}/chapters/{number:02d}-{title.lower().replace(' ', '-')}.wav"
    absolute_audio_path = Path(settings.OUTPUTS_PATH) / relative_audio_path
    _write_chapter_audio(absolute_audio_path, audio)

    chapter = Chapter(
        book_id=book.id,
        number=number,
        title=title,
        type=chapter_type,
        text_content=f"{title} narration text.",
        word_count=word_count,
        status=ChapterStatus.GENERATED,
        audio_path=relative_audio_path,
        duration_seconds=len(audio) / 1000.0,
        audio_file_size_bytes=absolute_audio_path.stat().st_size,
    )
    test_db.add(chapter)
    test_db.commit()
    test_db.refresh(chapter)
    return chapter


def _store_qa_record(
    test_db: Session,
    chapter: Chapter,
    *,
    overall_status: QAAutomaticStatus = QAAutomaticStatus.PASS,
    grade: str = "A",
) -> None:
    """Persist the chapter-level QA record required by Gate 3."""

    record = ChapterQARecord(
        book_id=chapter.book_id,
        chapter_n=chapter.number,
        overall_status=overall_status,
        qa_details=json.dumps(
            {
                "chapter_n": chapter.number,
                "book_id": chapter.book_id,
                "timestamp": "2026-03-25T00:00:00Z",
                "checks": [],
                "overall_status": overall_status.value,
                "chapter_report": {
                    "overall_grade": grade,
                },
            }
        ),
    )
    test_db.add(record)
    test_db.commit()


def _create_ready_chapter(
    test_db: Session,
    *,
    book: Book,
    number: int,
    title: str,
    audio: AudioSegment,
    word_count: int = 120,
    grade: str = "A",
    chapter_type: ChapterType = ChapterType.CHAPTER,
) -> Chapter:
    """Create a chapter that already passed Gate 2."""

    chapter = _create_chapter(
        test_db,
        book=book,
        number=number,
        title=title,
        audio=audio,
        word_count=word_count,
        chapter_type=chapter_type,
    )
    _store_qa_record(test_db, chapter, grade=grade)
    return chapter


def _create_credits(test_db: Session, book: Book) -> None:
    """Create opening and closing credits chapters for ACX preflight tests."""

    _create_ready_chapter(
        test_db,
        book=book,
        number=0,
        title="Opening Credits",
        audio=_with_edges(_tone(1500, 220, gain_db=-16.0)),
        chapter_type=ChapterType.OPENING_CREDITS,
    )
    _create_ready_chapter(
        test_db,
        book=book,
        number=99,
        title="Closing Credits",
        audio=_with_edges(_tone(1500, 220, gain_db=-16.0)),
        chapter_type=ChapterType.CLOSING_CREDITS,
    )


def test_loudness_consistency_pass(test_db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """Chapters inside the ±1.5 LU band should pass."""

    book = _create_book(test_db, title="Loudness Pass")
    chapter_one = _create_ready_chapter(test_db, book=book, number=1, title="One", audio=_with_edges(_tone(3000)))
    chapter_two = _create_ready_chapter(test_db, book=book, number=2, title="Two", audio=_with_edges(_tone(3000)))
    chapter_three = _create_ready_chapter(test_db, book=book, number=3, title="Three", audio=_with_edges(_tone(3000)))

    lufs_map = {
        chapter_one.number: -20.2,
        chapter_two.number: -19.6,
        chapter_three.number: -20.1,
    }
    monkeypatch.setattr(
        "src.pipeline.book_qa.measure_integrated_lufs",
        lambda audio_path: lufs_map[int(Path(audio_path).stem.split("-")[0])],
    )

    result = check_cross_chapter_loudness(book.id, test_db)

    assert result.status == "pass"
    assert result.details["max_deviation_lu"] <= 1.5


def test_loudness_consistency_fail(test_db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """A chapter more than 3 LU away should fail."""

    book = _create_book(test_db, title="Loudness Fail")
    chapter_one = _create_ready_chapter(test_db, book=book, number=1, title="One", audio=_with_edges(_tone(3000)))
    chapter_two = _create_ready_chapter(test_db, book=book, number=2, title="Two", audio=_with_edges(_tone(3000)))
    chapter_three = _create_ready_chapter(test_db, book=book, number=3, title="Three", audio=_with_edges(_tone(3000)))

    lufs_map = {
        chapter_one.number: -20.0,
        chapter_two.number: -20.2,
        chapter_three.number: -14.0,
    }
    monkeypatch.setattr(
        "src.pipeline.book_qa.measure_integrated_lufs",
        lambda audio_path: lufs_map[int(Path(audio_path).stem.split("-")[0])],
    )

    result = check_cross_chapter_loudness(book.id, test_db)

    assert result.status == "fail"
    assert result.blockers


def test_loudness_range_validation_flags_overcompressed_chapter(test_db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """LRA below 3 LU should fail the publishing-quality loudness range gate."""

    book = _create_book(test_db, title="LRA Fail")
    _create_ready_chapter(test_db, book=book, number=1, title="One", audio=_with_edges(_tone(3000)))
    _create_ready_chapter(test_db, book=book, number=2, title="Two", audio=_with_edges(_tone(3000)))
    lra_values = iter([6.0, 2.5])
    monkeypatch.setattr("src.pipeline.book_qa.measure_loudness_range_lu", lambda *_args, **_kwargs: next(lra_values))

    result = check_loudness_range(book.id, test_db)

    assert result.status == "fail"
    assert result.blockers


def test_voice_consistency_stable(test_db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """Stable voice fingerprints should pass."""

    book = _create_book(test_db, title="Voice Stable")
    chapters = [
        _create_ready_chapter(test_db, book=book, number=1, title="One", audio=_with_edges(_tone(3000))),
        _create_ready_chapter(test_db, book=book, number=2, title="Two", audio=_with_edges(_tone(3000))),
        _create_ready_chapter(test_db, book=book, number=3, title="Three", audio=_with_edges(_tone(3000))),
    ]
    fingerprints = iter(
        [
            {"mean_pitch_hz": 142.0, "pitch_range_hz": 5.0, "spectral_centroid": 2300.0, "speech_rate_wpm": 154.0, "mean_rms_db": -20.0, "spectral_bandwidth": 700.0},
            {"mean_pitch_hz": 143.0, "pitch_range_hz": 5.5, "spectral_centroid": 2320.0, "speech_rate_wpm": 155.0, "mean_rms_db": -20.1, "spectral_bandwidth": 705.0},
            {"mean_pitch_hz": 141.5, "pitch_range_hz": 5.2, "spectral_centroid": 2295.0, "speech_rate_wpm": 153.0, "mean_rms_db": -19.9, "spectral_bandwidth": 698.0},
        ]
    )
    monkeypatch.setattr("src.pipeline.book_qa.compute_voice_fingerprint", lambda *args, **kwargs: next(fingerprints))

    result = check_cross_chapter_voice(book.id, test_db)

    assert len(chapters) == 3
    assert result.status == "pass"
    assert result.details["outlier_chapters"] == []


def test_voice_consistency_drift(test_db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pitch drift beyond the threshold should be surfaced."""

    book = _create_book(test_db, title="Voice Drift")
    _create_ready_chapter(test_db, book=book, number=1, title="One", audio=_with_edges(_tone(3000)))
    _create_ready_chapter(test_db, book=book, number=2, title="Two", audio=_with_edges(_tone(3000)))
    _create_ready_chapter(test_db, book=book, number=3, title="Three", audio=_with_edges(_tone(3000)))
    fingerprints = iter(
        [
            {"mean_pitch_hz": 142.0, "pitch_range_hz": 5.0, "spectral_centroid": 2300.0, "speech_rate_wpm": 154.0, "mean_rms_db": -20.0, "spectral_bandwidth": 700.0},
            {"mean_pitch_hz": 141.0, "pitch_range_hz": 5.4, "spectral_centroid": 2290.0, "speech_rate_wpm": 153.0, "mean_rms_db": -20.0, "spectral_bandwidth": 698.0},
            {"mean_pitch_hz": 170.0, "pitch_range_hz": 8.2, "spectral_centroid": 2295.0, "speech_rate_wpm": 154.0, "mean_rms_db": -20.1, "spectral_bandwidth": 701.0},
        ]
    )
    monkeypatch.setattr("src.pipeline.book_qa.compute_voice_fingerprint", lambda *args, **kwargs: next(fingerprints))

    result = check_cross_chapter_voice(book.id, test_db)

    assert result.status == "warning"
    assert result.details["outlier_chapters"] == [3]


def test_pacing_consistency_even(test_db: Session) -> None:
    """Similar chapter WPM values should pass."""

    book = _create_book(test_db, title="Pacing Pass")
    _create_ready_chapter(test_db, book=book, number=1, title="One", audio=_with_edges(_tone(30_000)), word_count=75)
    _create_ready_chapter(test_db, book=book, number=2, title="Two", audio=_with_edges(_tone(32_000)), word_count=80)
    _create_ready_chapter(test_db, book=book, number=3, title="Three", audio=_with_edges(_tone(28_000)), word_count=70)

    result = check_cross_chapter_pacing(book.id, test_db)

    assert result.status == "pass"
    assert result.details["max_deviation_pct"] <= 10.0


def test_pacing_consistency_outlier(test_db: Session) -> None:
    """A chapter 25% faster than the rest should fail."""

    book = _create_book(test_db, title="Pacing Fail")
    _create_ready_chapter(test_db, book=book, number=1, title="One", audio=_with_edges(_tone(30_000)), word_count=75)
    _create_ready_chapter(test_db, book=book, number=2, title="Two", audio=_with_edges(_tone(30_000)), word_count=75)
    _create_ready_chapter(test_db, book=book, number=3, title="Three", audio=_with_edges(_tone(20_000)), word_count=75)

    result = check_cross_chapter_pacing(book.id, test_db)

    assert result.status == "fail"
    assert result.blockers


def test_chapter_transition_smooth(test_db: Session) -> None:
    """Matching chapter edges should pass transition QA."""

    book = _create_book(test_db, title="Transitions Pass")
    _create_ready_chapter(test_db, book=book, number=1, title="One", audio=_with_edges(_tone(4000, 220, gain_db=-18.0), lead_ms=1500, trail_ms=1500))
    _create_ready_chapter(test_db, book=book, number=2, title="Two", audio=_with_edges(_tone(4000, 220, gain_db=-18.5), lead_ms=1500, trail_ms=1500))

    result = check_chapter_transitions(book.id, test_db)

    assert result.status == "pass"
    assert result.details["issues"][0]["status"] == "pass"


def test_chapter_transition_jarring(test_db: Session) -> None:
    """A large chapter-edge energy jump should fail."""

    book = _create_book(test_db, title="Transitions Fail")
    _create_ready_chapter(test_db, book=book, number=1, title="One", audio=_with_edges(_tone(4000, 220, gain_db=-24.0)))
    _create_ready_chapter(test_db, book=book, number=2, title="Two", audio=_with_edges(_tone(4000, 220, gain_db=-8.0), lead_ms=100, trail_ms=1500))

    result = check_chapter_transitions(book.id, test_db)

    assert result.status == "fail"
    assert result.blockers


def test_chapter_transition_relaxes_threshold_for_credit_boundaries(test_db: Session) -> None:
    """Credit transitions should warn before they block on moderate energy jumps."""

    book = _create_book(test_db, title="Credit Transition Warning")
    _create_ready_chapter(
        test_db,
        book=book,
        number=0,
        title="Opening Credits",
        audio=_with_edges(_tone(4000, 220, gain_db=-25.0)),
        chapter_type=ChapterType.OPENING_CREDITS,
    )
    _create_ready_chapter(
        test_db,
        book=book,
        number=1,
        title="Chapter One",
        audio=_with_edges(_tone(4000, 220, gain_db=-17.0)),
        chapter_type=ChapterType.CHAPTER,
    )

    result = check_chapter_transitions(book.id, test_db)

    assert result.status == "warning"
    assert result.blockers == []
    assert result.details["issues"][0]["credits_transition"] is True
    assert result.details["issues"][0]["status"] == "warning"


def test_acx_compliance_pass(test_db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """A properly shaped chapter should satisfy ACX validation."""

    book = _create_book(test_db, title="ACX Pass")
    _create_credits(test_db, book)
    _create_ready_chapter(test_db, book=book, number=1, title="One", audio=_with_edges(_tone(3000, 220, gain_db=-18.0)))
    monkeypatch.setattr("src.pipeline.book_qa.measure_integrated_lufs", lambda *_args, **_kwargs: -20.0)

    result = check_acx_compliance(book.id, test_db)

    assert result.status == "pass"
    assert result.details["violations"] == []


def test_acx_compliance_peak_violation(test_db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """Peak levels above the ACX ceiling should fail."""

    book = _create_book(test_db, title="ACX Fail")
    _create_credits(test_db, book)
    _create_ready_chapter(test_db, book=book, number=1, title="One", audio=_with_edges(_tone(3000, 220, gain_db=-0.2)))
    monkeypatch.setattr("src.pipeline.book_qa.measure_integrated_lufs", lambda *_args, **_kwargs: -20.0)

    result = check_acx_compliance(book.id, test_db)

    assert result.status == "fail"
    assert any(violation["issue"] == "true_peak_db" for violation in result.details["violations"])


def test_acx_compliance_export_mode_downgrades_mastering_violations(
    test_db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Export mode should convert fixable ACX mastering issues into warnings."""

    book = _create_book(test_db, title="ACX Export Warning")
    _create_credits(test_db, book)
    _create_ready_chapter(test_db, book=book, number=1, title="One", audio=_with_edges(_tone(3000, 220, gain_db=-0.2)))
    monkeypatch.setattr("src.pipeline.book_qa.measure_integrated_lufs", lambda *_args, **_kwargs: -16.0)

    result = check_acx_compliance(book.id, test_db, export_mode=True)

    assert result.status == "warning"
    assert result.blockers == []
    assert any(violation["issue"] == "lufs" and violation["severity"] == "warning" for violation in result.details["violations"])
    assert any(violation["issue"] == "true_peak_db" and violation["severity"] == "warning" for violation in result.details["violations"])
    assert any("true_peak_db" in recommendation for recommendation in result.recommendations)


def test_acx_compliance_skips_long_wav_file_size_false_positive(
    test_db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Long intermediate WAV chapters should not fail the final ACX file-size check."""

    book = _create_book(test_db, title="ACX Long WAV")
    _create_credits(test_db, book)
    chapter = _create_ready_chapter(test_db, book=book, number=1, title="One", audio=_with_edges(_tone(3000, 220, gain_db=-18.0)))
    chapter.duration_seconds = 21 * 60
    test_db.commit()
    audio_path = Path(settings.OUTPUTS_PATH) / str(chapter.audio_path)
    real_stat = Path.stat

    def fake_stat(self: Path, *args, **kwargs):
        stat_result = real_stat(self, *args, **kwargs)
        if self != audio_path:
            return stat_result
        return os.stat_result(
            (
                stat_result.st_mode,
                stat_result.st_ino,
                stat_result.st_dev,
                stat_result.st_nlink,
                stat_result.st_uid,
                stat_result.st_gid,
                int(171.0 * 1024 * 1024),
                int(stat_result.st_atime),
                int(stat_result.st_mtime),
                int(stat_result.st_ctime),
            )
        )

    monkeypatch.setattr(Path, "stat", fake_stat)
    monkeypatch.setattr("src.pipeline.book_qa.measure_integrated_lufs", lambda *_args, **_kwargs: -20.0)

    result = check_acx_compliance(book.id, test_db)

    assert result.status == "pass"
    assert not any(violation["issue"] == "file_size_mb" for violation in result.details["violations"])


def test_book_qa_api_endpoint(client, test_db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """The book-level QA endpoint should return the expected Gate 3 payload."""

    book = _create_book(test_db, title="Book API")
    _create_ready_chapter(test_db, book=book, number=1, title="One", audio=_with_edges(_tone(3000)), grade="A")
    _create_ready_chapter(test_db, book=book, number=2, title="Two", audio=_with_edges(_tone(3000)), grade="B")
    monkeypatch.setattr("src.pipeline.book_qa.measure_integrated_lufs", lambda *_args, **_kwargs: -20.0)
    monkeypatch.setattr(
        "src.pipeline.book_qa.compute_voice_fingerprint",
        lambda *args, **kwargs: {
            "mean_pitch_hz": 142.0,
            "pitch_range_hz": 5.0,
            "spectral_centroid": 2300.0,
            "speech_rate_wpm": 154.0,
            "mean_rms_db": -20.0,
            "spectral_bandwidth": 700.0,
        },
    )

    response = client.get(f"/api/book/{book.id}/qa/book-report")

    assert response.status_code == 200
    payload = response.json()
    assert payload["book_id"] == book.id
    assert payload["title"] == book.title
    assert "cross_chapter_checks" in payload
    assert "loudness_consistency" in payload["cross_chapter_checks"]


def test_book_report_aggregates_grades_and_blockers(test_db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """The aggregate report should summarize chapter grades and export readiness."""

    book = _create_book(test_db, title="Book Summary")
    _create_credits(test_db, book)
    _create_ready_chapter(test_db, book=book, number=1, title="One", audio=_with_edges(_tone(3000)), grade="A")
    _create_ready_chapter(test_db, book=book, number=2, title="Two", audio=_with_edges(_tone(3000)), grade="C")
    monkeypatch.setattr("src.pipeline.book_qa.measure_integrated_lufs", lambda *_args, **_kwargs: -20.0)
    monkeypatch.setattr("src.pipeline.book_qa.measure_loudness_range_lu", lambda *_args, **_kwargs: 6.0)
    monkeypatch.setattr(
        "src.pipeline.book_qa.compute_voice_fingerprint",
        lambda *args, **kwargs: {
            "mean_pitch_hz": 142.0,
            "pitch_range_hz": 5.0,
            "spectral_centroid": 2300.0,
            "speech_rate_wpm": 154.0,
            "mean_rms_db": -20.0,
            "spectral_bandwidth": 700.0,
        },
    )

    report = run_book_qa(book.id, test_db)

    assert report.chapters_grade_a == 3
    assert report.chapters_grade_c == 1
    assert report.ready_for_export is True


def test_acx_compliance_reports_split_endpoint_for_oversized_chapter(
    test_db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Oversized chapters should point operators at the manual split endpoint."""

    monkeypatch.setattr("src.pipeline.book_qa.measure_integrated_lufs", lambda *_args, **_kwargs: -20.0)

    book = _create_book(test_db, title="Oversized Chapter")
    _create_credits(test_db, book)
    oversized = _create_ready_chapter(
        test_db,
        book=book,
        number=1,
        title="Huge Chapter",
        audio=_with_edges(_tone(3000)),
    )
    oversized.duration_seconds = ACX_REQUIREMENTS["max_chapter_duration_s"] + 60.0
    test_db.commit()

    result = check_acx_compliance(book.id, test_db)

    duration_violations = [
        violation
        for violation in result.details["violations"]
        if violation["issue"] == "duration_seconds_max"
    ]
    assert duration_violations
    assert duration_violations[0]["split_endpoint"] == f"/api/book/{book.id}/chapter/{oversized.id}/split"
    assert any("/api/book/" in blocker for blocker in result.blockers)


def test_export_readiness_summary_allows_warning_only_export(
    test_db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Grade C chapters should surface warnings without becoming blocking export failures."""

    monkeypatch.setattr("src.pipeline.book_qa.measure_integrated_lufs", lambda *_args, **_kwargs: -20.0)

    book = _create_book(test_db, title="Warning Export")
    _create_credits(test_db, book)
    _create_ready_chapter(
        test_db,
        book=book,
        number=1,
        title="Steady Chapter",
        audio=_with_edges(_tone(3000)),
        grade="A",
    )
    _create_ready_chapter(
        test_db,
        book=book,
        number=2,
        title="Needs Review",
        audio=_with_edges(_tone(3000)),
        grade="C",
    )

    summary = build_export_readiness_summary(book.id, test_db)

    assert summary["ready"] is False
    assert summary["export_anyway_allowed"] is True
    assert summary["blocking_issues"] == []
    assert any("Needs Review" in warning for warning in summary["warnings"])
