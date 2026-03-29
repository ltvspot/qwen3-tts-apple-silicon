"""Unit tests for chunk-level validation basics."""

from __future__ import annotations

import sys

from pydub import AudioSegment
from pydub.generators import Sine

from src.config import ChunkValidationSettings
from src.pipeline.chunk_validator import ChunkValidator, ValidationSeverity


def _tone(duration_ms: int, *, gain_db: float = -6.0, frame_rate: int = 22050) -> AudioSegment:
    """Create a mono tone fixture with a deterministic frame rate."""

    return (
        Sine(220)
        .to_audio_segment(duration=duration_ms, volume=gain_db)
        .set_frame_rate(frame_rate)
        .set_channels(1)
    )


def _validator(*, stt_alignment_enabled: bool = False) -> ChunkValidator:
    """Return a validator configured for deterministic unit tests."""

    return ChunkValidator(ChunkValidationSettings(stt_alignment_enabled=stt_alignment_enabled))


def test_detects_silent_audio() -> None:
    """Near-silent chunks should fail validation."""

    report = _validator().validate(AudioSegment.silent(duration=500, frame_rate=22050), "Hello world")

    assert report.worst_severity == ValidationSeverity.FAIL
    assert any("effectively silent" in issue.lower() for issue in report.issues)


def test_detects_clipping_risk() -> None:
    """Chunks at 0 dBFS should fail as clipped audio."""

    report = _validator().validate(_tone(1000, gain_db=0.0), "This is a clipping test.")

    assert report.worst_severity == ValidationSeverity.FAIL
    assert any("hard clipping" in issue.lower() for issue in report.issues)


def test_detects_too_short_audio() -> None:
    """Sub-100ms chunks should fail immediately."""

    report = _validator().validate(_tone(50), "This should be too short.")

    assert report.worst_severity == ValidationSeverity.FAIL
    assert any("too short" in issue.lower() for issue in report.issues)


def test_detects_too_long_audio() -> None:
    """Overlong chunks should fail duration validation."""

    report = _validator().validate(AudioSegment.silent(duration=120_001, frame_rate=22050), "Too long")

    assert report.worst_severity == ValidationSeverity.FAIL
    assert any("too long" in issue.lower() for issue in report.issues)


def test_detects_unexpected_sample_rate() -> None:
    """Non-standard sample rates should fail validation."""

    report = _validator().validate(_tone(1000, frame_rate=16000), "Unexpected sample rate test.")

    assert report.worst_severity == ValidationSeverity.FAIL
    assert any("unexpected sample rate" in issue.lower() for issue in report.issues)


def test_detects_sample_rate_mismatch() -> None:
    """Chunks should match the engine sample rate when one is provided."""

    report = _validator().validate(
        _tone(1000, frame_rate=22050),
        "Sample rate mismatch test.",
        expected_sample_rate=24000,
    )

    assert report.worst_severity == ValidationSeverity.FAIL
    assert any("sample rate mismatch" in issue.lower() for issue in report.issues)


def test_valid_audio_passes_non_alignment_checks_cleanly() -> None:
    """Balanced narration audio should pass when STT is disabled for the unit test."""

    report = _validator().validate(
        _tone(3500, gain_db=-6.0, frame_rate=22050),
        "This chunk has a reasonable duration and healthy audio levels for narration.",
        expected_sample_rate=22050,
    )

    assert report.worst_severity == ValidationSeverity.INFO
    assert report.needs_regeneration is False
    assert any("disabled in settings" in issue.lower() for issue in report.issues)


def test_chunk_validation_settings_default_to_large_turbo_model() -> None:
    """Chunk STT should default to the production mlx-whisper model and thresholds."""

    settings = ChunkValidationSettings()

    assert settings.stt_model == "mlx-community/whisper-large-v3-turbo"
    assert settings.wer_warning_threshold == 0.10
    assert settings.wer_fail_threshold == 0.20


def test_load_whisper_model_uses_mlx_whisper_backend_and_caches(monkeypatch) -> None:
    """The validator should import mlx-whisper once and reuse the cached backend handle."""

    fake_backend = object()
    monkeypatch.setitem(sys.modules, "mlx_whisper", fake_backend)
    ChunkValidator._whisper_model_cache.clear()
    ChunkValidator._whisper_import_failed = False
    ChunkValidator._whisper_model_loaded = False

    first = ChunkValidator._load_whisper_model("mlx-community/whisper-large-v3-turbo")
    second = ChunkValidator._load_whisper_model("mlx-community/whisper-large-v3-turbo")

    assert first is fake_backend
    assert second is fake_backend
    assert ChunkValidator._whisper_model_loaded is True

    ChunkValidator._whisper_model_cache.clear()
    ChunkValidator._whisper_import_failed = False
    ChunkValidator._whisper_model_loaded = False
