"""Tests for persistent voice cloning assets."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydub import AudioSegment
from pydub.generators import Sine

from src.engines.voice_cloner import VoiceCloner


def _export_reference_audio(tmp_path: Path, *, suffix: str, duration_ms: int = 2500) -> Path:
    """Create a short reference audio file in the requested format."""

    audio = Sine(220).to_audio_segment(duration=duration_ms).set_channels(1)
    reference_path = tmp_path / f"reference{suffix}"

    export_format = {
        ".wav": "wav",
        ".mp3": "mp3",
        ".m4a": "mp4",
    }[suffix]
    audio.export(reference_path, format=export_format)
    return reference_path


def test_clone_voice_wav(tmp_path: Path) -> None:
    """Cloning from WAV should persist WAV and transcript assets."""

    cloner = VoiceCloner(tmp_path / "voices")
    reference_path = _export_reference_audio(tmp_path, suffix=".wav")

    audio_path, transcript_path = cloner.clone_voice(
        "kent-zimering",
        reference_path,
        "This is a clear reference sample.",
    )

    assert Path(audio_path).exists()
    assert Path(transcript_path).exists()
    assert Path(transcript_path).read_text(encoding="utf-8") == "This is a clear reference sample."


def test_clone_voice_mp3(tmp_path: Path) -> None:
    """MP3 input should be converted into the canonical cloned WAV asset."""

    cloner = VoiceCloner(tmp_path / "voices")
    reference_path = _export_reference_audio(tmp_path, suffix=".mp3")

    audio_path, transcript_path = cloner.clone_voice(
        "mp3-clone",
        reference_path,
        "MP3 sample transcript.",
    )

    assert Path(audio_path).suffix == ".wav"
    assert Path(audio_path).exists()
    assert Path(transcript_path).exists()


def test_clone_voice_m4a(tmp_path: Path) -> None:
    """M4A input should be converted into the canonical cloned WAV asset."""

    cloner = VoiceCloner(tmp_path / "voices")
    reference_path = _export_reference_audio(tmp_path, suffix=".m4a")

    audio_path, transcript_path = cloner.clone_voice(
        "m4a-clone",
        reference_path,
        "M4A sample transcript.",
    )

    assert Path(audio_path).suffix == ".wav"
    assert Path(audio_path).exists()
    assert Path(transcript_path).exists()


def test_clone_voice_short_audio(tmp_path: Path) -> None:
    """Audio shorter than one second should be rejected."""

    cloner = VoiceCloner(tmp_path / "voices")
    reference_path = _export_reference_audio(tmp_path, suffix=".wav", duration_ms=500)

    with pytest.raises(ValueError, match="too short"):
        cloner.clone_voice("short-voice", reference_path, "Too short.")


def test_clone_voice_long_audio_warns(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Longer samples should log a warning but still persist."""

    cloner = VoiceCloner(tmp_path / "voices")
    reference_path = _export_reference_audio(tmp_path, suffix=".wav", duration_ms=12000)

    with caplog.at_level("WARNING"):
        audio_path, transcript_path = cloner.clone_voice(
            "long-voice",
            reference_path,
            "Long-form reference transcript.",
        )

    assert Path(audio_path).exists()
    assert Path(transcript_path).exists()
    assert "recommended" in caplog.text


def test_clone_voice_invalid_format(tmp_path: Path) -> None:
    """Unsupported file types should fail fast with a clear error."""

    cloner = VoiceCloner(tmp_path / "voices")
    invalid_file = tmp_path / "reference.txt"
    invalid_file.write_text("not audio", encoding="utf-8")

    with pytest.raises(ValueError, match="Unsupported audio format"):
        cloner.clone_voice("invalid-voice", invalid_file, "Transcript")


def test_list_cloned_voices(tmp_path: Path) -> None:
    """Only voices with both WAV and transcript files should be returned."""

    cloner = VoiceCloner(tmp_path / "voices")
    first_reference = _export_reference_audio(tmp_path, suffix=".wav")
    second_reference = _export_reference_audio(tmp_path, suffix=".mp3")

    cloner.clone_voice("alpha-voice", first_reference, "Alpha transcript.")
    cloner.clone_voice("beta-voice", second_reference, "Beta transcript.")
    orphan_wav = cloner.voices_dir / "orphan.wav"
    AudioSegment.silent(duration=1500).export(orphan_wav, format="wav")

    assert cloner.list_cloned_voices() == ["alpha-voice", "beta-voice"]


def test_delete_voice(tmp_path: Path) -> None:
    """Deleting a voice removes both cloned assets."""

    cloner = VoiceCloner(tmp_path / "voices")
    reference_path = _export_reference_audio(tmp_path, suffix=".wav")
    audio_path, transcript_path = cloner.clone_voice(
        "delete-me",
        reference_path,
        "Delete this voice.",
    )

    assert cloner.delete_voice("delete-me") is True
    assert not Path(audio_path).exists()
    assert not Path(transcript_path).exists()
    assert cloner.delete_voice("delete-me") is False
