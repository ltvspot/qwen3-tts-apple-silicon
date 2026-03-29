"""Helpers for text chunking and audio stitching."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import re
import unicodedata
from typing import TYPE_CHECKING

import numpy as np
from pydub.audio_segment import AudioSegment

if TYPE_CHECKING:
    from src.engines.pronunciation_dictionary import PronunciationDictionary

logger = logging.getLogger(__name__)
_DFT_MATRIX_CACHE: dict[int, np.ndarray] = {}
try:
    import grapheme  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - optional dependency
    grapheme = None


class TextChunker:
    """Split long narration text into chunk-safe segments."""

    @dataclass(slots=True)
    class ChunkPlan:
        """Text plus boundary metadata used for pause-aware stitching."""

        text: str
        ends_sentence: bool
        ends_paragraph: bool

    ABBREVIATIONS = {
        "mr",
        "mrs",
        "ms",
        "dr",
        "prof",
        "sr",
        "jr",
        "st",
        "ave",
        "blvd",
        "dept",
        "est",
        "fig",
        "gen",
        "gov",
        "govt",
        "inc",
        "ltd",
        "corp",
        "co",
        "vs",
        "etc",
        "approx",
        "appt",
        "apt",
        "assn",
        "assoc",
        "vol",
        "rev",
        "sgt",
        "cpl",
        "pvt",
        "capt",
        "lt",
        "col",
        "no",
        "nos",
        "p",
        "pp",
        "op",
        "ed",
        "trans",
        "repr",
    }
    _TITLE_ABBREVIATIONS = {
        "mr",
        "mrs",
        "ms",
        "dr",
        "prof",
        "sr",
        "jr",
        "st",
        "gen",
        "gov",
        "sgt",
        "cpl",
        "pvt",
        "capt",
        "lt",
        "col",
        "rev",
    }
    _CLOSING_PUNCTUATION = "\"')]}"
    ESTIMATED_WORDS_PER_SECOND = 2.6
    MIN_TARGET_CHUNK_SECONDS = 5.0
    MIN_TRAILING_CHUNK_SECONDS = 3.0
    OVERFLOW_DISABLED_MAX_CHARS = 100
    MAX_CHUNK_OVERFLOW_RATIO = 0.15
    MAX_CHUNK_OVERFLOW_CHARS = 80

    @classmethod
    def chunk_text(cls, text: str, max_chars: int) -> list[str]:
        """
        Split text into chunks that do not exceed ``max_chars``.

        The chunker prefers sentence boundaries first, then falls back to
        whitespace-aware token splitting for oversized sentences. Returned chunks
        preserve the original text when concatenated in order.
        """

        if max_chars < 1:
            raise ValueError("max_chars must be greater than zero")
        if not text:
            return [text]
        if len(text) <= max_chars:
            return [text]

        return [plan.text for plan in cls.chunk_text_with_metadata(text, max_chars)]

    @classmethod
    def chunk_text_with_metadata(cls, text: str, max_chars: int) -> list["TextChunker.ChunkPlan"]:
        """Split text into chunk plans while preserving sentence and paragraph boundaries."""

        if max_chars < 1:
            raise ValueError("max_chars must be greater than zero")
        if not text:
            return [cls.ChunkPlan(text=text, ends_sentence=False, ends_paragraph=False)]
        if len(text) <= max_chars:
            ends_paragraph = bool(re.search(r"\n\s*\n\s*$", text))
            ends_sentence = text.rstrip().endswith((".", "!", "?")) or ends_paragraph
            return [cls.ChunkPlan(text=text, ends_sentence=ends_sentence, ends_paragraph=ends_paragraph)]

        chunks: list[TextChunker.ChunkPlan] = []
        current_parts: list[str] = []
        current_length = 0
        current_ends_sentence = False
        current_ends_paragraph = False

        for segment in cls.split_into_sentences(text):
            ends_paragraph = bool(re.search(r"\n\s*\n\s*$", segment))
            segment_ends_sentence = segment.rstrip().endswith((".", "!", "?")) or ends_paragraph
            pieces = cls._split_oversized_segment(segment, max_chars)
            for piece_index, piece in enumerate(pieces):
                piece_length = len(piece)
                piece_ends_sentence = piece_index == len(pieces) - 1 and segment_ends_sentence
                piece_ends_paragraph = piece_index == len(pieces) - 1 and ends_paragraph
                if current_length + piece_length <= max_chars:
                    current_parts.append(piece)
                    current_length += piece_length
                    current_ends_sentence = piece_ends_sentence
                    current_ends_paragraph = piece_ends_paragraph
                    continue

                if current_parts:
                    chunks.append(
                        cls.ChunkPlan(
                            text="".join(current_parts),
                            ends_sentence=current_ends_sentence,
                            ends_paragraph=current_ends_paragraph,
                        )
                    )
                current_parts = [piece]
                current_length = piece_length
                current_ends_sentence = piece_ends_sentence
                current_ends_paragraph = piece_ends_paragraph

        if current_parts:
            chunks.append(
                cls.ChunkPlan(
                    text="".join(current_parts),
                    ends_sentence=current_ends_sentence,
                    ends_paragraph=current_ends_paragraph,
                )
            )

        return cls._merge_short_chunks(chunks, max_chars)

    @classmethod
    def preprocess_for_tts(
        cls,
        text: str,
        *,
        book_id: int | None = None,
        pronunciation_dictionary: "PronunciationDictionary | None" = None,
    ) -> str:
        """Apply pronunciation dictionary substitutions before synthesis."""

        del cls
        if pronunciation_dictionary is None or not text:
            return text
        return pronunciation_dictionary.replace_text(text, book_id=book_id)

    @classmethod
    def split_for_retry(cls, text: str) -> tuple[str, str] | None:
        """Split a failed chunk near the midpoint on the nearest sentence boundary."""

        sentences = cls.split_into_sentences(text)
        if len(sentences) < 2:
            return None

        target = len(text) / 2
        cumulative = 0
        best_index: int | None = None
        best_distance: float | None = None
        for index, sentence in enumerate(sentences[:-1], start=1):
            cumulative += len(sentence)
            distance = abs(cumulative - target)
            if best_distance is None or distance < best_distance:
                best_index = index
                best_distance = distance

        if best_index is None:
            return None

        left = "".join(sentences[:best_index]).strip()
        right = "".join(sentences[best_index:]).strip()
        if not left or not right:
            return None
        return (left, right)

    @classmethod
    def split_into_sentences(cls, text: str) -> list[str]:
        """Split text into sentence-like segments while preserving whitespace."""

        if not text:
            return [text]

        sentences: list[str] = []
        segment_start = 0
        index = 0

        while index < len(text):
            character = text[index]
            if character not in ".!?":
                index += 1
                continue

            if character == "." and cls._is_abbreviation(text[:index], text[index + 1 :]):
                index += 1
                continue

            boundary_end = index + 1
            while boundary_end < len(text) and text[boundary_end] in cls._CLOSING_PUNCTUATION:
                boundary_end += 1

            whitespace_end = boundary_end
            while whitespace_end < len(text) and text[whitespace_end].isspace():
                whitespace_end += 1

            if whitespace_end == boundary_end and whitespace_end < len(text):
                index += 1
                continue

            sentences.append(text[segment_start:whitespace_end])
            segment_start = whitespace_end
            index = whitespace_end

        if segment_start < len(text):
            sentences.append(text[segment_start:])

        return sentences or [text]

    @classmethod
    def _split_sentences(cls, text: str) -> list[str]:
        """Backwards-compatible wrapper for internal sentence splitting."""

        return cls.split_into_sentences(text)

    @classmethod
    def _is_abbreviation(cls, text_before_period: str, text_after_period: str = "") -> bool:
        """Return whether the current period belongs to an abbreviation or decimal."""

        stripped = text_before_period.rstrip()
        if not stripped:
            return False

        if cls._is_decimal_number(stripped, text_after_period):
            return True

        words = stripped.split()
        if not words:
            return False

        last_word = words[-1].lower().rstrip(".")
        next_word = cls._next_word(text_after_period)

        if last_word in cls._TITLE_ABBREVIATIONS:
            return True
        if last_word in cls.ABBREVIATIONS:
            return bool(next_word) and (next_word[0].islower() or next_word[0].isdigit())
        if len(last_word) == 1 and last_word.isalpha():
            return bool(next_word) and next_word[0].isupper()
        return False

    @staticmethod
    def _is_decimal_number(text_before_period: str, text_after_period: str) -> bool:
        """Return whether the current period sits inside a decimal number."""

        if not text_before_period or not text_after_period:
            return False
        return text_before_period[-1].isdigit() and text_after_period[0].isdigit()

    @staticmethod
    def _next_word(text_after_period: str) -> str:
        """Return the next token after a period, ignoring whitespace and quotes."""

        match = re.match(r"[\s'\"\)\]\}]*([A-Za-z0-9][\w-]*)", text_after_period)
        return match.group(1) if match else ""

    @staticmethod
    def _split_oversized_segment(segment: str, max_chars: int) -> list[str]:
        """Split a long segment on whitespace and finally on grapheme-safe character boundaries."""

        if TextChunker._text_length(segment) <= max_chars:
            return [segment]

        pieces: list[str] = []
        current = ""
        tokens = re.findall(r"\S+\s*|\s+", segment)

        for token in tokens:
            if TextChunker._text_length(token) > max_chars:
                if current:
                    pieces.append(current)
                    current = ""
                pieces.extend(TextChunker._split_token_grapheme_safe(token, max_chars))
                continue

            if TextChunker._text_length(current) + TextChunker._text_length(token) <= max_chars:
                current += token
                continue

            if current:
                pieces.append(current)
            current = token

        if current:
            pieces.append(current)

        return pieces

    @staticmethod
    def _text_length(text: str) -> int:
        """Return the effective character length, preferring grapheme clusters when available."""

        if grapheme is not None:
            return int(grapheme.length(text))
        return len(unicodedata.normalize("NFC", text))

    @staticmethod
    def _split_token_grapheme_safe(token: str, max_chars: int) -> list[str]:
        """Split a token without breaking grapheme clusters or decomposed accent sequences."""

        if grapheme is not None:
            graphemes = list(grapheme.graphemes(token))
            return [
                "".join(graphemes[start:start + max_chars])
                for start in range(0, len(graphemes), max_chars)
            ]

        normalized = unicodedata.normalize("NFC", token)
        return [normalized[start:start + max_chars] for start in range(0, len(normalized), max_chars)]

    @classmethod
    def _merge_short_chunks(
        cls,
        chunks: list["TextChunker.ChunkPlan"],
        max_chars: int,
    ) -> list["TextChunker.ChunkPlan"]:
        """Prefer fewer, longer chunks when a tiny fragment would create a weak stitch."""

        if len(chunks) < 2:
            return chunks

        merged = list(chunks)
        merge_limit = cls._merge_limit(max_chars)
        index = 0
        while index < len(merged) - 1:
            current = merged[index]
            if cls._estimate_audio_seconds(current.text) >= cls.MIN_TARGET_CHUNK_SECONDS:
                index += 1
                continue

            candidate = cls._combine_chunk_plans(current, merged[index + 1])
            if cls._text_length(candidate.text) > merge_limit:
                index += 1
                continue

            merged[index:index + 2] = [candidate]

        if len(merged) >= 2 and cls._estimate_audio_seconds(merged[-1].text) < cls.MIN_TRAILING_CHUNK_SECONDS:
            candidate = cls._combine_chunk_plans(merged[-2], merged[-1])
            if cls._text_length(candidate.text) <= merge_limit:
                merged[-2:] = [candidate]

        return merged

    @classmethod
    def _merge_limit(cls, max_chars: int) -> int:
        """Allow slight overflow on conservative production limits to absorb tiny tails."""

        if max_chars < cls.OVERFLOW_DISABLED_MAX_CHARS:
            return max_chars

        overflow = min(
            cls.MAX_CHUNK_OVERFLOW_CHARS,
            max(1, int(round(max_chars * cls.MAX_CHUNK_OVERFLOW_RATIO))),
        )
        return max_chars + overflow

    @classmethod
    def _estimate_audio_seconds(cls, text: str) -> float:
        """Estimate narration duration using the engine's baseline pacing heuristic."""

        word_count = len([word for word in text.split() if word.strip()])
        if word_count == 0:
            return 0.0
        return word_count / cls.ESTIMATED_WORDS_PER_SECOND

    @classmethod
    def _combine_chunk_plans(cls, left: "TextChunker.ChunkPlan", right: "TextChunker.ChunkPlan") -> "TextChunker.ChunkPlan":
        """Merge two adjacent chunk plans while preserving the end-boundary metadata."""

        del cls
        return TextChunker.ChunkPlan(
            text=left.text + right.text,
            ends_sentence=right.ends_sentence,
            ends_paragraph=right.ends_paragraph,
        )


class AudioStitcher:
    """Join audio chunks together with a light crossfade."""

    CROSSFADE_MS = 30
    SIMILAR_CROSSFADE_MS = 20
    MODERATE_CROSSFADE_MS = 50
    DIFFERENT_CROSSFADE_MS = 100
    VERY_DIFFERENT_CROSSFADE_MS = 150
    SHORT_CHUNK_MS = 5000
    SHORT_CHUNK_CROSSFADE_MULTIPLIER = 1.5
    BOUNDARY_FADE_MS = 3
    CROSSFADE_LPF_CUTOFF_HZ = 8000.0

    @dataclass(slots=True)
    class StitchResult:
        """Stitch output plus the chunk start timestamps used for QA."""

        audio: AudioSegment
        chunk_boundaries: list[float]
        crossfades_ms: list[int]

    @classmethod
    def compute_adaptive_crossfade(cls, chunk_a: AudioSegment, chunk_b: AudioSegment) -> int:
        """Choose a crossfade based on the spectral similarity around a stitch point."""

        tail = chunk_a[-100:].set_channels(1)
        head = chunk_b[:100].set_channels(1)
        if len(tail) == 0 or len(head) == 0:
            return cls.CROSSFADE_MS

        sample_count = min(len(tail.get_array_of_samples()), len(head.get_array_of_samples()))
        if sample_count <= 0:
            return cls.CROSSFADE_MS

        tail_samples = cls._normalized_samples(tail)[:sample_count]
        head_samples = cls._normalized_samples(head)[:sample_count]
        tail_spectrum = cls._magnitude_spectrum(tail_samples)
        head_spectrum = cls._magnitude_spectrum(head_samples)
        similarity = cls._cosine_similarity(tail_spectrum, head_spectrum)

        if similarity > 0.9:
            crossfade = cls.SIMILAR_CROSSFADE_MS
        elif similarity >= 0.7:
            crossfade = cls.MODERATE_CROSSFADE_MS
        elif similarity >= 0.5:
            crossfade = cls.DIFFERENT_CROSSFADE_MS
        else:
            logger.warning("Very different chunk boundary detected (similarity %.2f); using 150ms crossfade", similarity)
            crossfade = cls.VERY_DIFFERENT_CROSSFADE_MS

        if len(chunk_a) < cls.SHORT_CHUNK_MS or len(chunk_b) < cls.SHORT_CHUNK_MS:
            crossfade = int(round(crossfade * cls.SHORT_CHUNK_CROSSFADE_MULTIPLIER))

        return crossfade

    @classmethod
    def stitch_with_metadata(cls, audio_chunks: list[AudioSegment]) -> StitchResult:
        """Return stitched audio plus chunk boundary metadata for downstream QA."""

        return cls.stitch_with_metadata_and_pauses(audio_chunks, pause_after_ms=None)

    @classmethod
    def stitch_with_metadata_and_pauses(
        cls,
        audio_chunks: list[AudioSegment],
        *,
        pause_after_ms: list[int] | None,
    ) -> StitchResult:
        """Return stitched audio plus chunk boundary metadata with optional inserted pauses."""

        if not audio_chunks:
            raise ValueError("No audio chunks to stitch")
        if len(audio_chunks) == 1:
            return cls.StitchResult(audio=audio_chunks[0], chunk_boundaries=[0.0], crossfades_ms=[])

        logger.info("Stitching %s audio chunks", len(audio_chunks))
        result = audio_chunks[0]
        chunk_boundaries = [0.0]
        crossfades_ms: list[int] = []
        previous_chunk = audio_chunks[0]

        pauses = list(pause_after_ms or [])

        for index, chunk in enumerate(audio_chunks[1:], start=1):
            prior_pause_ms = pauses[index - 1] if index - 1 < len(pauses) else 0
            if prior_pause_ms > 0:
                pause = AudioSegment.silent(duration=prior_pause_ms, frame_rate=result.frame_rate).set_channels(1)
                result += pause
                chunk_boundaries.append(len(result) / 1000.0)
                crossfades_ms.append(0)
                result += chunk
                previous_chunk = chunk
                continue

            adaptive_crossfade = cls.compute_adaptive_crossfade(previous_chunk, chunk)
            crossfade = min(adaptive_crossfade, len(result), len(chunk))
            chunk_boundaries.append(max(len(result) - crossfade, 0) / 1000.0)
            crossfades_ms.append(crossfade)
            result = cls._append_with_smoothed_crossfade(result, chunk, crossfade)
            previous_chunk = chunk

        return cls.StitchResult(
            audio=result,
            chunk_boundaries=chunk_boundaries,
            crossfades_ms=crossfades_ms,
        )

    @classmethod
    def stitch(cls, audio_chunks: list[AudioSegment], *, pause_after_ms: list[int] | None = None) -> AudioSegment:
        """Return one continuous segment from one or more generated chunks."""

        return cls.stitch_with_metadata_and_pauses(audio_chunks, pause_after_ms=pause_after_ms).audio

    @staticmethod
    def _normalized_samples(audio: AudioSegment) -> np.ndarray:
        """Return mono float samples in the range [-1, 1]."""

        mono_audio = audio.set_channels(1)
        samples = np.array(mono_audio.get_array_of_samples(), dtype=np.float32)
        if samples.size == 0:
            return samples

        max_amplitude = float(1 << ((8 * mono_audio.sample_width) - 1))
        return samples / max_amplitude

    @classmethod
    def _append_with_smoothed_crossfade(
        cls,
        leading: AudioSegment,
        trailing: AudioSegment,
        crossfade_ms: int,
    ) -> AudioSegment:
        """Crossfade two chunks with tiny boundary fades and transient smoothing."""

        trailing = cls._match_audio_format(trailing, leading)
        overlap_ms = min(crossfade_ms, len(leading), len(trailing))
        if overlap_ms <= 0:
            return leading + trailing

        fade_ms = min(cls.BOUNDARY_FADE_MS, overlap_ms)
        left_tail = leading[-overlap_ms:]
        right_head = trailing[:overlap_ms]
        if fade_ms > 0:
            left_tail = left_tail.fade_out(fade_ms)
            right_head = right_head.fade_in(fade_ms)

        overlap = left_tail.overlay(right_head)
        overlap = cls._butterworth_low_pass(overlap, cutoff_hz=cls.CROSSFADE_LPF_CUTOFF_HZ)
        return leading[:-overlap_ms] + overlap + trailing[overlap_ms:]

    @staticmethod
    def _match_audio_format(audio: AudioSegment, template: AudioSegment) -> AudioSegment:
        """Align audio format before combining raw sample buffers."""

        matched = audio
        if matched.frame_rate != template.frame_rate:
            matched = matched.set_frame_rate(template.frame_rate)
        if matched.channels != template.channels:
            matched = matched.set_channels(template.channels)
        if matched.sample_width != template.sample_width:
            matched = matched.set_sample_width(template.sample_width)
        return matched

    @classmethod
    def _butterworth_low_pass(cls, segment: AudioSegment, *, cutoff_hz: float) -> AudioSegment:
        """Apply a gentle 2nd-order Butterworth-style low-pass filter to a segment."""

        if len(segment) == 0:
            return segment

        dtype = cls._sample_dtype(segment.sample_width)
        if dtype is None:
            return segment

        cutoff_hz = min(cutoff_hz, (segment.frame_rate / 2.0) - 1.0)
        if cutoff_hz <= 0:
            return segment

        samples = np.array(segment.get_array_of_samples(), dtype=np.float32)
        if samples.size == 0:
            return segment

        channels = segment.channels
        reshaped = samples.reshape((-1, channels)) if channels > 1 else samples.reshape((-1, 1))
        max_amplitude = float(1 << ((8 * segment.sample_width) - 1))
        normalized = reshaped / max_amplitude
        filtered = np.empty_like(normalized)

        q = 1.0 / np.sqrt(2.0)
        omega = (2.0 * np.pi * cutoff_hz) / float(segment.frame_rate)
        sin_omega = np.sin(omega)
        cos_omega = np.cos(omega)
        alpha = sin_omega / (2.0 * q)

        b0 = (1.0 - cos_omega) / 2.0
        b1 = 1.0 - cos_omega
        b2 = (1.0 - cos_omega) / 2.0
        a0 = 1.0 + alpha
        a1 = -2.0 * cos_omega
        a2 = 1.0 - alpha

        b0 /= a0
        b1 /= a0
        b2 /= a0
        a1 /= a0
        a2 /= a0

        for channel_index in range(normalized.shape[1]):
            source = normalized[:, channel_index]
            destination = filtered[:, channel_index]
            x1 = x2 = y1 = y2 = 0.0
            for sample_index, sample in enumerate(source):
                output = (b0 * sample) + (b1 * x1) + (b2 * x2) - (a1 * y1) - (a2 * y2)
                destination[sample_index] = output
                x2 = x1
                x1 = sample
                y2 = y1
                y1 = output

        clipped = np.clip(filtered * max_amplitude, np.iinfo(dtype).min, np.iinfo(dtype).max).astype(dtype)
        flattened = clipped.reshape(-1)
        return segment._spawn(flattened.tobytes())

    @staticmethod
    def _sample_dtype(sample_width: int) -> np.dtype | None:
        """Return a numpy dtype compatible with the segment's PCM sample width."""

        if sample_width == 1:
            return np.int8
        if sample_width == 2:
            return np.int16
        if sample_width == 4:
            return np.int32
        return None

    @staticmethod
    def _magnitude_spectrum(samples: np.ndarray) -> np.ndarray:
        """Return a stable real-spectrum magnitude without relying on ``numpy.fft``."""

        frame_length = samples.size
        if frame_length == 0:
            return np.zeros(0, dtype=np.float32)

        if frame_length not in _DFT_MATRIX_CACHE:
            frequencies = np.arange((frame_length // 2) + 1, dtype=np.float32)[:, None]
            times = np.arange(frame_length, dtype=np.float32)[None, :]
            exponent = (-2j * np.pi * frequencies * times) / float(frame_length)
            _DFT_MATRIX_CACHE[frame_length] = np.exp(exponent).astype(np.complex64)

        return np.abs(_DFT_MATRIX_CACHE[frame_length] @ samples.astype(np.float32))

    @staticmethod
    def _cosine_similarity(left: np.ndarray, right: np.ndarray) -> float:
        """Return cosine similarity between two spectra."""

        if left.size == 0 or right.size == 0:
            return 1.0

        sample_count = min(left.size, right.size)
        left = left[:sample_count]
        right = right[:sample_count]
        left_norm = float(np.linalg.norm(left))
        right_norm = float(np.linalg.norm(right))
        if left_norm <= 1e-8 or right_norm <= 1e-8:
            return 1.0

        return float(np.dot(left, right) / (left_norm * right_norm))
