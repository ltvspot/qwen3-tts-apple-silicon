"""Tests for the expanded automated QA checks."""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest
from pydub import AudioSegment
from pydub.generators import Sine

from src.pipeline.qa_checker import (
    check_lufs_compliance,
    check_pacing_consistency,
    check_stitch_clicks,
)

FRAME_RATE = 22050


def _tone(duration_ms: int, *, gain_db: float = -18.0) -> AudioSegment:
    return Sine(220).to_audio_segment(duration=duration_ms).apply_gain(gain_db).set_frame_rate(FRAME_RATE).set_channels(1)


def _spike(duration_ms: int = 3, amplitude: float = 0.999) -> AudioSegment:
    sample_count = int(FRAME_RATE * (duration_ms / 1000))
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
