"""Persistent cloned-voice asset management."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from pydub import AudioSegment

from src.config import settings

logger = logging.getLogger(__name__)

VOICE_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")


class VoiceCloner:
    """Persist and resolve reference assets for voice cloning."""

    SUPPORTED_FORMATS = {".wav", ".mp3", ".m4a"}

    def __init__(self, voices_dir: str | Path | None = None) -> None:
        """Initialize the voice asset directory."""

        self.voices_dir = Path(voices_dir or settings.VOICES_PATH)
        self.voices_dir.mkdir(parents=True, exist_ok=True)

    def clone_voice(
        self,
        voice_name: str,
        reference_audio_path: str | Path,
        transcript: str,
    ) -> tuple[str, str]:
        """Create or overwrite a persistent cloned voice reference."""

        canonical_name = self.validate_voice_name(voice_name)
        cleaned_transcript = transcript.strip()
        if not cleaned_transcript:
            raise ValueError("Transcript cannot be empty.")

        wav_path = self._convert_to_wav(reference_audio_path, canonical_name)
        duration_seconds = self.get_audio_duration(wav_path)
        if duration_seconds < 1.0:
            wav_path.unlink(missing_ok=True)
            raise ValueError(f"Reference audio too short: {duration_seconds:.1f}s (minimum 1s)")
        if duration_seconds > 10.0:
            logger.warning(
                "Reference audio for '%s' is %.1fs long; 3-10 seconds is recommended.",
                canonical_name,
                duration_seconds,
            )

        transcript_path = self.transcript_path(canonical_name)
        transcript_path.write_text(cleaned_transcript, encoding="utf-8")

        logger.info(
            "Voice cloned: %s (audio=%s, transcript=%s, duration=%.1fs)",
            canonical_name,
            wav_path,
            transcript_path,
            duration_seconds,
        )
        return (str(wav_path), str(transcript_path))

    def list_cloned_voices(self) -> list[str]:
        """Return all cloned voice IDs that have both WAV and transcript assets."""

        voices: list[str] = []
        for wav_path in sorted(self.voices_dir.glob("*.wav")):
            transcript_path = self.transcript_path(wav_path.stem)
            if transcript_path.exists():
                voices.append(wav_path.stem)
        return voices

    def has_voice(self, voice_name: str) -> bool:
        """Return whether a cloned voice exists on disk."""

        return self._canonical_existing_name(voice_name) is not None

    def get_voice_assets(self, voice_name: str) -> dict[str, str] | None:
        """Return persisted clone assets for a voice when they exist."""

        canonical_name = self._canonical_existing_name(voice_name)
        if canonical_name is None:
            return None

        audio_path = self.audio_path(canonical_name)
        transcript_path = self.transcript_path(canonical_name)
        if not audio_path.exists() or not transcript_path.exists():
            return None

        return {
            "voice_name": canonical_name,
            "ref_audio_path": str(audio_path),
            "transcript": transcript_path.read_text(encoding="utf-8"),
            "transcript_path": str(transcript_path),
        }

    def delete_voice(self, voice_name: str) -> bool:
        """Delete a cloned voice's persisted reference files."""

        canonical_name = self._canonical_existing_name(voice_name)
        if canonical_name is None:
            return False

        self.audio_path(canonical_name).unlink(missing_ok=True)
        self.transcript_path(canonical_name).unlink(missing_ok=True)
        logger.info("Deleted cloned voice assets for '%s'", canonical_name)
        return True

    def get_audio_duration(self, audio_path: str | Path) -> float:
        """Return the duration for a saved reference audio file in seconds."""

        audio = AudioSegment.from_wav(str(audio_path))
        return len(audio) / 1000.0

    def audio_path(self, voice_name: str) -> Path:
        """Return the cloned voice WAV path for a canonical voice ID."""

        return self.voices_dir / f"{voice_name}.wav"

    def transcript_path(self, voice_name: str) -> Path:
        """Return the cloned voice transcript path for a canonical voice ID."""

        return self.voices_dir / f"{voice_name}.txt"

    def validate_voice_name(self, voice_name: str) -> str:
        """Validate and normalize a user-provided cloned voice ID."""

        normalized = voice_name.strip()
        if not normalized:
            raise ValueError("Voice name cannot be empty.")
        if not VOICE_NAME_PATTERN.fullmatch(normalized):
            raise ValueError(
                "Voice name must use lowercase letters, numbers, hyphens, or underscores only."
            )
        return normalized

    def _convert_to_wav(self, input_path: str | Path, voice_name: str) -> Path:
        """Convert a supported input file into the canonical cloned voice WAV asset."""

        input_file = Path(input_path)
        if not input_file.exists():
            raise ValueError(f"Reference audio not found: {input_file}")
        if input_file.suffix.lower() not in self.SUPPORTED_FORMATS:
            raise ValueError(
                f"Unsupported audio format: {input_file.suffix or 'unknown'}. "
                "Use WAV, MP3, or M4A."
            )

        output_path = self.audio_path(voice_name)
        try:
            audio = AudioSegment.from_file(str(input_file))
            audio = audio.set_channels(1)
            audio.export(str(output_path), format="wav")
        except Exception as exc:
            raise ValueError(f"Failed to convert audio to WAV: {exc}") from exc
        return output_path

    def _canonical_existing_name(self, voice_name: str) -> str | None:
        """Resolve a cloned voice ID case-insensitively against persisted assets."""

        requested = voice_name.strip().lower()
        if not requested:
            return None

        for candidate in self.list_cloned_voices():
            if candidate.lower() == requested:
                return candidate
        return None
