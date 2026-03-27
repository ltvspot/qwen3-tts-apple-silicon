"""Unit tests for the deep audio QA quality analyzer."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from src.pipeline.audio_qa.audio_quality_analyzer import AudioQualityAnalyzer
from src.pipeline.audio_qa.models import DependencyNotice


def test_quality_analyzer_reports_missing_dependency(tmp_path: Path, monkeypatch) -> None:
    """The analyzer should degrade cleanly when audio decoding is unavailable."""

    audio_path = tmp_path / "chapter.wav"
    audio_path.write_bytes(b"fake")
    analyzer = AudioQualityAnalyzer()
    monkeypatch.setattr(analyzer, "_load_audio", lambda path: (_ for _ in ()).throw(RuntimeError("soundfile missing")))

    result = analyzer.analyze(audio_path)

    assert result.status == "dependency_unavailable"
    assert result.dependency.available is False
    assert result.dependency.dependency == "soundfile"


def test_quality_analyzer_passes_nominal_audio(tmp_path: Path, monkeypatch) -> None:
    """Nominal mono audio should produce a passing quality score."""

    audio_path = tmp_path / "chapter.wav"
    audio_path.write_bytes(b"fake")
    analyzer = AudioQualityAnalyzer()
    samples = np.concatenate(
        [
            np.full(4000, 0.005, dtype=np.float32),
            np.full(8000, 0.08, dtype=np.float32),
            np.full(4000, 0.005, dtype=np.float32),
        ]
    )
    monkeypatch.setattr(analyzer, "_load_audio", lambda path: (samples, 16000))
    monkeypatch.setattr(
        analyzer,
        "_measure_loudness",
        lambda path, mono, sr: (-20.5, 4.2, DependencyNotice(dependency="pyloudnorm", available=True)),
    )

    result = analyzer.analyze(audio_path)

    assert result.status == "pass"
    assert result.integrated_lufs == -20.5
    assert result.peak_dbfs < -10.0
    assert result.score >= 90.0


def test_quality_analyzer_flags_clipping_with_timestamps(tmp_path: Path, monkeypatch) -> None:
    """Clipped runs should be exposed as timestamped artifact issues."""

    audio_path = tmp_path / "chapter.wav"
    audio_path.write_bytes(b"fake")
    analyzer = AudioQualityAnalyzer()
    samples = np.zeros(1000, dtype=np.float32)
    samples[200:240] = 1.0
    monkeypatch.setattr(analyzer, "_load_audio", lambda path: (samples, 1000))
    monkeypatch.setattr(
        analyzer,
        "_measure_loudness",
        lambda path, mono, sr: (-20.0, 3.0, DependencyNotice(dependency="pyloudnorm", available=True)),
    )

    result = analyzer.analyze(audio_path)

    clipping_issue = next(issue for issue in result.artifact_events if issue.code == "clipping_event")
    assert clipping_issue.start_time_seconds == 0.2
    assert clipping_issue.end_time_seconds == 0.24
    assert result.status == "fail"


def test_quality_analyzer_flags_low_snr(tmp_path: Path, monkeypatch) -> None:
    """A narrow signal/noise gap should lower the quality score."""

    audio_path = tmp_path / "chapter.wav"
    audio_path.write_bytes(b"fake")
    analyzer = AudioQualityAnalyzer()
    rng = np.random.default_rng(42)
    samples = rng.normal(0, 0.05, 16000).astype(np.float32)
    samples[::4] += 0.02
    monkeypatch.setattr(analyzer, "_load_audio", lambda path: (samples, 16000))
    monkeypatch.setattr(
        analyzer,
        "_measure_loudness",
        lambda path, mono, sr: (-20.0, 5.0, DependencyNotice(dependency="pyloudnorm", available=True)),
    )

    result = analyzer.analyze(audio_path)

    snr_issue = next(issue for issue in result.issues if issue.code == "low_snr")
    assert snr_issue.severity in {"warning", "error"}
    assert result.snr_db is not None


def test_quality_analyzer_flags_loudness_out_of_range(tmp_path: Path, monkeypatch) -> None:
    """Off-target LUFS should be reported even when other metrics look clean."""

    audio_path = tmp_path / "chapter.wav"
    audio_path.write_bytes(b"fake")
    analyzer = AudioQualityAnalyzer()
    samples = np.full(16000, 0.04, dtype=np.float32)
    monkeypatch.setattr(analyzer, "_load_audio", lambda path: (samples, 16000))
    monkeypatch.setattr(
        analyzer,
        "_measure_loudness",
        lambda path, mono, sr: (-13.0, 6.0, DependencyNotice(dependency="pyloudnorm", available=True)),
    )

    result = analyzer.analyze(audio_path)

    loudness_issue = next(issue for issue in result.issues if issue.code == "loudness_out_of_range")
    assert loudness_issue.severity == "error"
    assert result.status == "fail"
