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
    check_breath_levels,
    check_lufs_compliance,
    check_pacing_consistency,
    check_plosive_artifacts,
    check_room_tone_padding,
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


def _low_burst(duration_ms: int = 10, *, gain_db: float = -2.0) -> AudioSegment:
    return Sine(80).to_audio_segment(duration=duration_ms).apply_gain(gain_db).set_frame_rate(FRAME_RATE).set_channels(1)


def _noise_burst(duration_ms: int, *, peak_dbfs: float) -> AudioSegment:
    sample_count = max(int(FRAME_RATE * (duration_ms / 1000.0)), 1)
    rng = np.random.default_rng(800 + duration_ms + int(abs(peak_dbfs) * 10))
    samples = rng.standard_normal(sample_count).astype(np.float32)
    samples /= max(float(np.max(np.abs(samples))), 1.0)
    samples *= float(10 ** (peak_dbfs / 20.0))
    int_samples = np.round(samples * np.iinfo(np.int16).max).astype(np.int16)
    return AudioSegment(
        data=int_samples.tobytes(),
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


def test_check_plosive_artifacts_warns_when_rate_exceeds_threshold(tmp_path: Path) -> None:
    """Moderate plosive rates should warn before they block release."""

    audio = _tone(30_000, gain_db=-30.0)
    audio = audio.overlay(_low_burst(), position=5_000)
    audio = audio.overlay(_low_burst(), position=15_000)
    audio_path = tmp_path / "plosive-warning.wav"
    audio.export(audio_path, format="wav")

    result = check_plosive_artifacts(audio_path)

    assert result.status == "warning"
    assert result.details is not None
    assert result.details["plosives_per_minute"] > 3.0


def test_check_plosive_artifacts_fails_when_rate_is_high(tmp_path: Path) -> None:
    """Frequent plosive pops should fail the publishing QA gate."""

    audio = _tone(30_000, gain_db=-30.0)
    for position in (2_000, 8_000, 14_000, 20_000, 26_000):
        audio = audio.overlay(_low_burst(), position=position)
    audio_path = tmp_path / "plosive-fail.wav"
    audio.export(audio_path, format="wav")

    result = check_plosive_artifacts(audio_path)

    assert result.status == "fail"
    assert result.details is not None
    assert result.details["plosives_per_minute"] > 8.0


def test_check_breath_levels_warns_on_loud_breath(tmp_path: Path) -> None:
    """Breaths above -25 dBFS should warn for manual cleanup."""

    audio = AudioSegment.silent(duration=300, frame_rate=FRAME_RATE) + _noise_burst(250, peak_dbfs=-22.0) + _tone(1000)
    audio_path = tmp_path / "breath-warning.wav"
    audio.export(audio_path, format="wav")

    result = check_breath_levels(audio_path)

    assert result.status == "warning"
    assert result.details is not None
    assert result.details["max_peak_dbfs"] > -25.0


def test_check_breath_levels_fails_on_very_loud_breath(tmp_path: Path) -> None:
    """Breaths above -20 dBFS should fail publishing QA."""

    audio = AudioSegment.silent(duration=300, frame_rate=FRAME_RATE) + _noise_burst(250, peak_dbfs=-18.0) + _tone(1000)
    audio_path = tmp_path / "breath-fail.wav"
    audio.export(audio_path, format="wav")

    result = check_breath_levels(audio_path)

    assert result.status == "fail"
    assert result.details is not None
    assert result.details["max_peak_dbfs"] > -20.0


def test_check_room_tone_padding_passes_quiet_edges(tmp_path: Path) -> None:
    """Low-level edge padding should pass the room-tone gate."""

    audio = _noise_burst(500, peak_dbfs=-60.0) + _tone(1200) + _noise_burst(1000, peak_dbfs=-60.0)
    audio_path = tmp_path / "room-tone-pass.wav"
    audio.export(audio_path, format="wav")

    result = check_room_tone_padding(audio_path)

    assert result.status == "pass"


def test_check_room_tone_padding_fails_when_speech_hits_the_edge(tmp_path: Path) -> None:
    """Speech at the file edge should fail room-tone validation."""

    audio_path = tmp_path / "room-tone-fail.wav"
    _tone(1500).export(audio_path, format="wav")

    result = check_room_tone_padding(audio_path)

    assert result.status == "fail"


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
