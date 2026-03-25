"""Tests for the chapter-level quality gate introduced in prompt 25."""

from __future__ import annotations

from pathlib import Path

from pydub import AudioSegment
from pydub.generators import Sine

from src.engines.chunker import AudioStitcher
from src.pipeline.qa_checker import (
    ChapterQAReport,
    QACheckResult,
    check_contextual_silence,
    check_pacing_detailed,
    check_spectral_quality,
    check_stitch_quality,
    check_voice_consistency,
)

FRAME_RATE = 22050


def _tone(duration_ms: int, frequency_hz: int = 220, *, gain_db: float = -18.0) -> AudioSegment:
    """Return a mono test tone segment."""

    return (
        Sine(frequency_hz)
        .to_audio_segment(duration=duration_ms)
        .apply_gain(gain_db)
        .set_frame_rate(FRAME_RATE)
        .set_channels(1)
    )


def _write_audio(path: Path, audio: AudioSegment) -> Path:
    """Export a WAV fixture and return its path."""

    audio.export(path, format="wav")
    return path


def test_voice_consistency_stable(tmp_path: Path) -> None:
    """Consistent chunk metrics should pass the voice consistency check."""

    audio = _tone(1000, 220) + _tone(1000, 220) + _tone(1000, 220)
    audio_path = _write_audio(tmp_path / "voice-stable.wav", audio)

    result = check_voice_consistency(audio_path, [0.0, 1.0, 2.0])

    assert result.status == "pass"
    assert result.value == 0


def test_voice_consistency_drift(tmp_path: Path) -> None:
    """A drifting chunk should be surfaced as a voice consistency warning."""

    audio = _tone(1000, 220) + _tone(1000, 220) + _tone(1000, 260)
    audio_path = _write_audio(tmp_path / "voice-drift.wav", audio)

    result = check_voice_consistency(audio_path, [0.0, 1.0, 2.0])

    assert result.status == "warning"
    assert result.value is not None
    assert result.value > 0.15


def test_spectral_no_hum(tmp_path: Path) -> None:
    """Clean speech-like audio should pass spectral quality checks."""

    audio_path = _write_audio(tmp_path / "spectral-clean.wav", _tone(2500, 220))

    result = check_spectral_quality(audio_path)

    assert result.status == "pass"


def test_spectral_hum_detected(tmp_path: Path) -> None:
    """A strong 60Hz component should trigger the hum warning."""

    hum_audio = _tone(2500, 60, gain_db=-8.0)
    audio_path = _write_audio(tmp_path / "spectral-hum.wav", hum_audio)

    result = check_spectral_quality(audio_path)

    assert result.status == "warning"
    assert result.details is not None
    assert result.details["hum_frequency_hz"] == 60.0


def test_contextual_silence_paragraph(tmp_path: Path) -> None:
    """A 1.2 second paragraph pause should pass contextual silence QA."""

    text = "The first paragraph ends here.\n\nThe second paragraph begins now."
    audio = _tone(2000, 220) + AudioSegment.silent(duration=1200, frame_rate=FRAME_RATE) + _tone(2000, 220)
    audio_path = _write_audio(tmp_path / "paragraph-silence.wav", audio)

    result = check_contextual_silence(audio_path, text, [0.0, 2.0])

    assert result.status == "pass"


def test_contextual_silence_mid_sentence(tmp_path: Path) -> None:
    """The same pause in the middle of a sentence should warn."""

    text = "This sentence keeps going without a natural break until much later in the line."
    audio = _tone(2000, 220) + AudioSegment.silent(duration=1200, frame_rate=FRAME_RATE) + _tone(2000, 220)
    audio_path = _write_audio(tmp_path / "mid-sentence-silence.wav", audio)

    result = check_contextual_silence(audio_path, text, [0.0, 2.0])

    assert result.status == "warning"
    assert result.details is not None
    assert result.details["violations"][0]["context"] == "mid_sentence"


def test_stitch_tonal_discontinuity(tmp_path: Path) -> None:
    """A sharp tonal change at a stitch boundary should warn."""

    audio = _tone(1000, 220) + _tone(1000, 880)
    audio_path = _write_audio(tmp_path / "stitch-tone.wav", audio)

    result = check_stitch_quality(audio_path, [0.0, 1.0])

    assert result.status == "warning"
    assert result.details is not None
    assert any(issue["type"] == "tonal_discontinuity" for issue in result.details["issues"])


def test_stitch_quality_single_chunk_passes(tmp_path: Path) -> None:
    """Single-chunk chapters should bypass stitch-boundary analysis."""

    audio_path = _write_audio(tmp_path / "single-chunk.wav", _tone(1500, 220))

    result = check_stitch_quality(audio_path, [0.0])

    assert result.status == "pass"
    assert result.details is not None
    assert result.details["stitch_quality"]["total_stitches"] == 0


def test_pacing_consistent(tmp_path: Path) -> None:
    """Evenly spoken windows should pass the detailed pacing check."""

    audio_path = _write_audio(tmp_path / "pacing-consistent.wav", _tone(30_000, 220))
    text = "word " * 90

    result = check_pacing_detailed(audio_path, text)

    assert result.status == "pass"
    assert result.value == 0


def test_pacing_inconsistent(tmp_path: Path) -> None:
    """A sparse middle window should fail the stricter pacing gate."""

    audio = _tone(10_000, 220) + _tone(2000, 220) + AudioSegment.silent(duration=8000, frame_rate=FRAME_RATE) + _tone(10_000, 220)
    audio_path = _write_audio(tmp_path / "pacing-inconsistent.wav", audio)
    text = "word " * 90

    result = check_pacing_detailed(audio_path, text)

    assert result.status == "fail"
    assert result.value is not None
    assert result.value > 0.4


def test_adaptive_crossfade_similar() -> None:
    """Similar chunks should get the shortest adaptive crossfade."""

    crossfade = AudioStitcher.compute_adaptive_crossfade(_tone(1000, 220), _tone(1000, 220))

    assert crossfade == AudioStitcher.SIMILAR_CROSSFADE_MS


def test_adaptive_crossfade_different() -> None:
    """Spectrally different chunks should get a long adaptive crossfade."""

    crossfade = AudioStitcher.compute_adaptive_crossfade(_tone(1000, 220), _tone(1000, 880))

    assert crossfade >= AudioStitcher.DIFFERENT_CROSSFADE_MS


def test_chapter_qa_grade() -> None:
    """The chapter QA report should expose the required export grade logic."""

    report = ChapterQAReport(
        chapter_number=1,
        chapter_title="Grade Test",
        duration_seconds=12.5,
        total_checks=9,
        passed=7,
        warnings=2,
        failures=0,
        results=[
            QACheckResult(name="file_exists", status="pass", message="ok"),
            QACheckResult(name="voice_consistency", status="warning", message="drift"),
        ],
        pacing_stats={"mean_wpm": 180.0},
        silence_stats={"count": 1},
        stitch_quality={"total_stitches": 2},
    )

    assert report.overall_grade == "B"
    assert report.ready_for_export is True
