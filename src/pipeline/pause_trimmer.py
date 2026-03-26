"""Detect and trim excessive mid-chunk silences to natural lengths."""

from __future__ import annotations

from pydub import AudioSegment
from pydub.silence import detect_silence

MAX_PAUSE_MS = 1500
TRIM_TARGET_MS = 800
EDGE_PRESERVE_MS = 400


class PauseTrimmer:
    """Trim excessive silences within a generated chunk."""

    @classmethod
    def trim_excessive_pauses(
        cls,
        audio: AudioSegment,
        max_pause_ms: int = MAX_PAUSE_MS,
        trim_target_ms: int = TRIM_TARGET_MS,
        silence_thresh_db: int = -40,
        preserve_ranges_ms: list[tuple[int, int]] | None = None,
    ) -> tuple[AudioSegment, int]:
        """Trim internal silences that exceed the allowed mid-chunk pause length."""
        edge_preserve_ms = min(EDGE_PRESERVE_MS, max(trim_target_ms // 2, 0))
        protected_ranges = preserve_ranges_ms or []

        silences = detect_silence(
            audio,
            min_silence_len=max_pause_ms,
            silence_thresh=silence_thresh_db,
        )

        if not silences:
            return (audio, 0)

        trimmed = audio
        count = 0
        for start_ms, end_ms in reversed(silences):
            silence_duration = end_ms - start_ms
            if silence_duration <= max_pause_ms:
                continue
            if any(start_ms < protected_end and end_ms > protected_start for protected_start, protected_end in protected_ranges):
                continue

            keep_before = start_ms + edge_preserve_ms
            keep_after = end_ms - edge_preserve_ms
            if keep_before >= keep_after:
                continue

            trimmed = trimmed[:keep_before] + trimmed[keep_after:]
            count += 1

        return (trimmed, count)
