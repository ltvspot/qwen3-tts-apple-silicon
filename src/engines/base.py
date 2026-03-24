"""Abstract interfaces and shared types for text-to-speech engines."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from pydub.audio_segment import AudioSegment


@dataclass(slots=True)
class Voice:
    """Represents a TTS voice exposed by an engine."""

    name: str
    description: str | None = None
    language: str = "en-US"


@dataclass(slots=True)
class AudioGenerationConfig:
    """Configuration for a single audio generation request."""

    text: str
    voice: str
    emotion: str | None = None
    speed: float = 1.0
    sample_rate: int = 22050


class TTSEngine(ABC):
    """
    Abstract base class for TTS engines.

    Concrete adapters implement this interface so the rest of the pipeline can
    switch engines without changing application logic.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the stable engine identifier."""

    @property
    @abstractmethod
    def max_chunk_chars(self) -> int:
        """Return the largest text chunk this engine can safely synthesize at once."""

    @property
    @abstractmethod
    def supports_emotion(self) -> bool:
        """Return whether the engine accepts style or emotion instructions."""

    @property
    @abstractmethod
    def supports_cloning(self) -> bool:
        """Return whether the engine can synthesize from a cloned voice reference."""

    @abstractmethod
    def load(self) -> None:
        """Load engine resources into memory and validate runtime dependencies."""

    @abstractmethod
    def unload(self) -> None:
        """Release engine resources and any loaded model state."""

    @abstractmethod
    def list_voices(self) -> list[Voice]:
        """Return the voices currently available through this engine."""

    @abstractmethod
    def generate(
        self,
        text: str,
        voice: str,
        emotion: str | None = None,
        speed: float = 1.0,
    ) -> AudioSegment:
        """
        Generate speech audio for the given text and voice.

        Raises:
            ValueError: If the request arguments are invalid.
            RuntimeError: If the model is unavailable or generation fails.
        """

    @abstractmethod
    def estimate_duration(self, text: str, speed: float = 1.0) -> float:
        """Return a best-effort duration estimate in seconds for the provided text."""

    def clone_voice(
        self,
        ref_audio_path: str,
        transcript: str,
        output_voice_name: str,
    ) -> str:
        """
        Clone a voice from reference audio when the engine supports it.

        Engines that do not implement cloning should keep the default behavior.
        """

        raise NotImplementedError(f"{self.name} does not support voice cloning")
