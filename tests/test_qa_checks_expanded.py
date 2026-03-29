"""Tests for the expanded automated QA checks."""

from __future__ import annotations

import asyncio
import subprocess
import time
from pathlib import Path

import numpy as np
import pytest
from pydub import AudioSegment
from pydub.generators import Sine

from src.database import Chapter, ChapterStatus, ChapterType
from src.pipeline.qa_checker import (
    QACheckResult,
    check_lufs_compliance,
    check_pacing_consistency,
    check_stitch_clicks,
    run_qa_checks_for_chapter,
)

FRAME_RATE = 22050


def _tone(duration_ms: int, *, gain_db: float = -18.0) -> AudioSegment:
    return Sine(220).to_audio_segment(duration=duration_ms).apply_gain(gain_db).set_frame_rate(FRAME_RATE).set_channels(1)


def _spike(duration_ms: float = 3, amplitude: float = 0.999) -> AudioSegment:
    sample_count = max(1, int(FRAME_RATE * (duration_ms / 1000)))
    samples = np.full(sample_count, int(np.iinfo(np.int16).max * amplitude), dtype=np.int16)
    return AudioSegment(
        data=samples.tobytes(),
        sample_width=2,
        frame_rate=FRAME_RATE,
        channels=1,
    )


def test_check_stitch_clicks_passes_clean_audio() -> None:
    """Steady audio should not trigger click detection."""

    result = check_stitch_clicks(_tone(1500))

    assert result.status == "pass"
    assert result.value == 0


def test_check_stitch_clicks_warns_on_single_click() -> None:
    """A single isolated spike should be surfaced as a warning."""

    audio = _tone(500) + _spike() + _tone(500)

    result = check_stitch_clicks(audio)

    assert result.status == "warning"
    assert result.value == pytest.approx(1.0)


def test_check_stitch_clicks_fails_on_repeated_clicks() -> None:
    """Multiple click regions should fail the stitch-boundary check."""

    audio = _tone(300) + _spike() + _tone(300) + _spike() + _tone(300) + _spike() + _tone(300)

    result = check_stitch_clicks(audio)

    assert result.status == "fail"
    assert result.value >= 3


def test_check_stitch_clicks_uses_ratio_based_failure_when_stitch_count_is_known() -> None:
    """Hard clicks should fail once they affect more than a quarter of stitch points."""

    audio = _tone(300) + _spike() + _tone(300) + _spike() + _tone(300)

    result = check_stitch_clicks(audio, chapter_duration_seconds=180.0, total_stitches=4)

    assert result.status == "fail"
    assert result.details is not None
    assert result.details["click_ratio"] == pytest.approx(0.5)


def test_check_stitch_clicks_relaxes_short_chapter_thresholds() -> None:
    """Short chapters should treat 12-15dB peaks as warnings instead of hard failures."""

    audio = _tone(300) + _spike(amplitude=0.55) + _tone(300) + _spike(amplitude=0.55) + _tone(300)

    result = check_stitch_clicks(audio, chapter_duration_seconds=60.0, total_stitches=4)

    assert result.status == "warning"
    assert result.details is not None
    assert result.details["hard_clicks"] == 0
    assert result.details["threshold_db"] == pytest.approx(15.0)


def test_check_stitch_clicks_marks_micro_clicks_as_warnings() -> None:
    """Sub-millisecond 12-15dB peaks should be tracked as micro-click warnings."""

    audio = _tone(500) + _spike(duration_ms=0.5, amplitude=0.5) + _tone(500)

    result = check_stitch_clicks(audio, chapter_duration_seconds=180.0, total_stitches=4)

    assert result.status == "warning"
    assert result.details is not None
    assert result.details["micro_clicks"] >= 1
    assert result.details["hard_clicks"] == 0


def test_check_pacing_consistency_passes_balanced_audio() -> None:
    """Consistent speech density across windows should pass."""

    audio = _tone(30_000)
    text = "word " * 90

    result = check_pacing_consistency(audio, text)

    assert result.status == "pass"
    assert result.value == 0


def test_check_pacing_consistency_warns_on_large_window_variance() -> None:
    """A sparse speech window should be surfaced as a pacing anomaly."""

    audio = _tone(10_000) + _tone(2000) + AudioSegment.silent(duration=8000, frame_rate=FRAME_RATE) + _tone(10_000)
    text = "word " * 90

    result = check_pacing_consistency(audio, text)

    assert result.status == "warning"
    assert result.value is not None
    assert result.value > 0.4


def test_check_lufs_compliance_passes_when_in_range(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """LUFS within ACX range should pass."""

    monkeypatch.setattr("src.pipeline.qa_checker.shutil.which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(
        "src.pipeline.qa_checker.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout="",
            stderr='{\n  "input_i" : "-19.1"\n}',
        ),
    )

    result = check_lufs_compliance(tmp_path / "book.mp3")

    assert result.status == "pass"
    assert result.value == pytest.approx(-19.1)


def test_check_lufs_compliance_warns_when_near_range(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Slightly off-target loudness should warn instead of fail."""

    monkeypatch.setattr("src.pipeline.qa_checker.shutil.which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(
        "src.pipeline.qa_checker.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout="",
            stderr='{\n  "input_i" : "-17.2"\n}',
        ),
    )

    result = check_lufs_compliance(tmp_path / "book.m4b")

    assert result.status == "warning"
    assert result.value == pytest.approx(-17.2)


def test_check_lufs_compliance_fails_when_far_outside_range(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Severely non-compliant loudness should fail."""

    monkeypatch.setattr("src.pipeline.qa_checker.shutil.which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(
        "src.pipeline.qa_checker.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout="",
            stderr='{\n  "input_i" : "-14.0"\n}',
        ),
    )

    result = check_lufs_compliance(tmp_path / "book.m4b")

    assert result.status == "fail"
    assert result.value == pytest.approx(-14.0)


@pytest.mark.asyncio
async def test_run_qa_checks_for_chapter_times_out_to_fast_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Timed-out full QA should fall back to the bounded fast QA path."""

    audio_path = tmp_path / "timeout-chapter.wav"
    _tone(1500).export(audio_path, format="wav")
    chapter = Chapter(
        book_id=1,
        number=1,
        title="Timeout Chapter",
        type=ChapterType.CHAPTER,
        status=ChapterStatus.GENERATED,
        audio_path=str(audio_path),
        word_count=3,
        text_content="one two three",
    )

    def slow_full_qa(**kwargs):
        del kwargs
        time.sleep(0.05)
        raise AssertionError("full QA should have timed out")

    monkeypatch.setattr("src.pipeline.qa_checker._run_qa_checks_sync", slow_full_qa)
    monkeypatch.setattr("src.pipeline.qa_checker.QA_CHAPTER_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(
        "src.pipeline.qa_checker.check_lufs_compliance",
        lambda *args, **kwargs: QACheckResult(
            name="lufs_compliance",
            status="pass",
            message="Synthetic LUFS pass.",
            value=-19.0,
        ),
    )

    result = await run_qa_checks_for_chapter(chapter)

    assert "timeout" in result.notes.lower()
    assert [check.name for check in result.checks] == [
        "file_exists",
        "duration_check",
        "clipping_detection",
        "lufs_compliance",
    ]
