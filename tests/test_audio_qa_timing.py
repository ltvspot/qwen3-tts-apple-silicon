"""Unit tests for the deep audio QA timing analyzer."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from src.pipeline.audio_qa.timing_analyzer import TimingAndPacingAnalyzer


class _FakeEffects:
    """Minimal librosa.effects facade used in unit tests."""

    def __init__(self, intervals: np.ndarray) -> None:
        self._intervals = intervals

    def split(self, samples, top_db, frame_length, hop_length):  # noqa: ANN001
        del samples, top_db, frame_length, hop_length
        return self._intervals


class _FakeLibrosa:
    """Minimal librosa-like backend used for deterministic tests."""

    def __init__(self, samples: np.ndarray, sample_rate: int, intervals: np.ndarray) -> None:
        self._samples = samples
        self._sample_rate = sample_rate
        self.effects = _FakeEffects(intervals)

    def load(self, path, sr=None, mono=True):  # noqa: ANN001
        del path, sr, mono
        return self._samples, self._sample_rate


def test_timing_analyzer_reports_missing_dependency(tmp_path: Path, monkeypatch) -> None:
    """The analyzer should degrade cleanly when librosa is unavailable."""

    audio_path = tmp_path / "chapter.wav"
    audio_path.write_bytes(b"fake")
    analyzer = TimingAndPacingAnalyzer()
    monkeypatch.setattr(analyzer, "_load_backend", lambda: (_ for _ in ()).throw(RuntimeError("librosa missing")))

    result = analyzer.analyze(audio_path, "Hello world")

    assert result.status == "dependency_unavailable"
    assert result.dependency.available is False
    assert result.issues[0].code == "missing_librosa"


def test_timing_analyzer_scores_balanced_chapter(tmp_path: Path, monkeypatch) -> None:
    """Nominal pacing with no long pauses should pass."""

    audio_path = tmp_path / "chapter.wav"
    audio_path.write_bytes(b"fake")
    sample_rate = 100
    samples = np.ones(sample_rate * 6, dtype=np.float32)
    intervals = np.array([[0, len(samples)]], dtype=np.int64)
    backend = _FakeLibrosa(samples, sample_rate, intervals)
    analyzer = TimingAndPacingAnalyzer()
    monkeypatch.setattr(analyzer, "_load_backend", lambda: backend)

    text = " ".join(["word"] * 14)
    result = analyzer.analyze(audio_path, text)

    assert result.status == "pass"
    assert result.actual_duration_seconds == 6.0
    assert result.speech_rate_wpm == 140.0
    assert result.pause_ratio == 0.0
    assert result.pauses == []
    assert result.score >= 90.0


def test_timing_analyzer_flags_mid_chapter_pause(tmp_path: Path, monkeypatch) -> None:
    """A long silence in the middle of a chapter should create a timestamped issue."""

    audio_path = tmp_path / "chapter.wav"
    audio_path.write_bytes(b"fake")
    sample_rate = 100
    samples = np.ones(sample_rate * 5, dtype=np.float32)
    intervals = np.array([[0, 100], [280, 500]], dtype=np.int64)
    backend = _FakeLibrosa(samples, sample_rate, intervals)
    analyzer = TimingAndPacingAnalyzer()
    monkeypatch.setattr(analyzer, "_load_backend", lambda: backend)

    result = analyzer.analyze(audio_path, " ".join(["word"] * 11))

    pause_issue = next(issue for issue in result.pauses if issue.code == "mid_pause")
    assert pause_issue.start_time_seconds == 1.0
    assert pause_issue.end_time_seconds == 2.8
    assert pause_issue.severity == "warning"
    assert result.status == "warning"


def test_timing_analyzer_flags_duration_mismatch(tmp_path: Path, monkeypatch) -> None:
    """Large text/audio drift should be called out explicitly."""

    audio_path = tmp_path / "chapter.wav"
    audio_path.write_bytes(b"fake")
    sample_rate = 100
    samples = np.ones(sample_rate * 20, dtype=np.float32)
    intervals = np.array([[0, len(samples)]], dtype=np.int64)
    backend = _FakeLibrosa(samples, sample_rate, intervals)
    analyzer = TimingAndPacingAnalyzer()
    monkeypatch.setattr(analyzer, "_load_backend", lambda: backend)

    result = analyzer.analyze(audio_path, "short text only")

    duration_issue = next(issue for issue in result.issues if issue.code == "duration_mismatch")
    assert duration_issue.severity == "error"
    assert result.status == "fail"


def test_timing_analyzer_flags_fast_speech_rate(tmp_path: Path, monkeypatch) -> None:
    """Overly dense text in a short clip should trigger a pace issue."""

    audio_path = tmp_path / "chapter.wav"
    audio_path.write_bytes(b"fake")
    sample_rate = 100
    samples = np.ones(sample_rate * 5, dtype=np.float32)
    intervals = np.array([[0, len(samples)]], dtype=np.int64)
    backend = _FakeLibrosa(samples, sample_rate, intervals)
    analyzer = TimingAndPacingAnalyzer()
    monkeypatch.setattr(analyzer, "_load_backend", lambda: backend)

    result = analyzer.analyze(audio_path, " ".join(["word"] * 16))

    pace_issue = next(issue for issue in result.issues if issue.code == "speech_rate_out_of_range")
    assert pace_issue.severity == "warning"
    assert result.speech_rate_wpm == 192.0
    assert result.status == "warning"
