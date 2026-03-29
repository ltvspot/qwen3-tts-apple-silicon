"""Unit tests for the deep audio QA transcription checker."""

from __future__ import annotations

from pathlib import Path

from src.pipeline.audio_qa.transcription_checker import AudioQADependencyError, TranscriptionAccuracyChecker


def test_transcription_checker_defaults_to_large_turbo_model() -> None:
    """Deep QA transcription should default to the production Whisper model and thresholds."""

    checker = TranscriptionAccuracyChecker()

    assert checker.model_name == "mlx-community/whisper-large-v3-turbo"
    assert checker.WARNING_WER_THRESHOLD == 0.10
    assert checker.SEGMENT_WARNING_THRESHOLD == 0.25
    assert checker.SEGMENT_ERROR_THRESHOLD == 0.45


def test_transcription_checker_reports_missing_dependency(tmp_path: Path, monkeypatch) -> None:
    """The checker should degrade cleanly when mlx-whisper is unavailable."""

    audio_path = tmp_path / "chapter.wav"
    audio_path.write_bytes(b"fake")
    checker = TranscriptionAccuracyChecker()
    monkeypatch.setattr(checker, "_load_backend", lambda: (_ for _ in ()).throw(AudioQADependencyError("mlx missing")))

    result = checker.analyze(audio_path, "Hello world")

    assert result.status == "dependency_unavailable"
    assert result.dependency.available is False
    assert result.issues[0].code == "missing_mlx_whisper"


def test_transcription_checker_returns_fail_for_missing_audio(monkeypatch, tmp_path: Path) -> None:
    """The checker should fail fast when the audio file does not exist."""

    checker = TranscriptionAccuracyChecker()
    result = checker.analyze(tmp_path / "missing.wav", "Hello world")

    assert result.status == "failed"
    assert result.issues[0].code == "missing_audio_file"


def test_transcription_checker_scores_identical_transcript(monkeypatch, tmp_path: Path) -> None:
    """Identical text should produce a perfect score and no diff entries."""

    audio_path = tmp_path / "chapter.wav"
    audio_path.write_bytes(b"fake")
    checker = TranscriptionAccuracyChecker()
    monkeypatch.setattr(checker, "_load_backend", lambda: object())
    monkeypatch.setattr(
        checker,
        "_transcribe",
        lambda backend, path: {
            "text": "Hello world from chapter one",
            "segments": [{"start": 0.0, "end": 1.2, "text": "Hello world from chapter one"}],
        },
    )

    result = checker.analyze(audio_path, "Hello world from chapter one")

    assert result.status == "pass"
    assert result.word_error_rate == 0.0
    assert result.score == 100.0
    assert result.diff == []


def test_transcription_checker_creates_diff_for_mismatched_words(monkeypatch, tmp_path: Path) -> None:
    """Mismatched output should create diff entries and a failing score."""

    audio_path = tmp_path / "chapter.wav"
    audio_path.write_bytes(b"fake")
    checker = TranscriptionAccuracyChecker()
    monkeypatch.setattr(checker, "_load_backend", lambda: object())
    monkeypatch.setattr(
        checker,
        "_transcribe",
        lambda backend, path: {
            "text": "hello world from chapter two",
            "segments": [{"start": 0.0, "end": 1.0, "text": "hello world from chapter two"}],
        },
    )

    result = checker.analyze(audio_path, "hello world from chapter one")

    assert result.status == "fail"
    assert result.word_error_rate == 0.2
    assert result.score == 80.0
    assert result.diff[0].operation == "replace"
    assert any(issue.code == "chapter_alignment" for issue in result.issues)


def test_transcription_checker_adds_timestamped_segment_issue(monkeypatch, tmp_path: Path) -> None:
    """Segment-level drift should create timestamped issues for the frontend."""

    audio_path = tmp_path / "chapter.wav"
    audio_path.write_bytes(b"fake")
    checker = TranscriptionAccuracyChecker()
    monkeypatch.setattr(checker, "_load_backend", lambda: object())
    monkeypatch.setattr(
        checker,
        "_transcribe",
        lambda backend, path: {
            "text": "alpha beta random words",
            "segments": [
                {"start": 0.0, "end": 0.5, "text": "alpha beta"},
                {"start": 0.5, "end": 1.1, "text": "random words"},
            ],
        },
    )

    result = checker.analyze(audio_path, "alpha beta gamma delta")

    mismatch_issue = next(issue for issue in result.issues if issue.code == "segment_mismatch")

    assert mismatch_issue.start_time_seconds == 0.5
    assert mismatch_issue.end_time_seconds == 1.1
    assert mismatch_issue.details["actual_excerpt"] == "random words"


def test_transcription_checker_marks_empty_reference_as_skipped(tmp_path: Path) -> None:
    """An empty chapter reference should skip STT scoring instead of crashing."""

    audio_path = tmp_path / "chapter.wav"
    audio_path.write_bytes(b"fake")
    checker = TranscriptionAccuracyChecker()

    result = checker.analyze(audio_path, "   ")

    assert result.status == "skipped"
    assert result.issues[0].code == "empty_reference"
