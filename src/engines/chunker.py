"""Helpers for text chunking and audio stitching."""

from __future__ import annotations

import logging
import re

from pydub.audio_segment import AudioSegment

logger = logging.getLogger(__name__)


class TextChunker:
    """Split long narration text into chunk-safe segments."""

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

        chunks: list[str] = []
        current_parts: list[str] = []
        current_length = 0

        for segment in cls.split_into_sentences(text):
            for piece in cls._split_oversized_segment(segment, max_chars):
                piece_length = len(piece)
                if current_length + piece_length <= max_chars:
                    current_parts.append(piece)
                    current_length += piece_length
                    continue

                if current_parts:
                    chunks.append("".join(current_parts))
                current_parts = [piece]
                current_length = piece_length

        if current_parts:
            chunks.append("".join(current_parts))

        return chunks

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
        """Split a long segment on whitespace and finally on raw character boundaries."""

        if len(segment) <= max_chars:
            return [segment]

        pieces: list[str] = []
        current = ""
        tokens = re.findall(r"\S+\s*|\s+", segment)

        for token in tokens:
            if len(token) > max_chars:
                if current:
                    pieces.append(current)
                    current = ""
                for start in range(0, len(token), max_chars):
                    pieces.append(token[start : start + max_chars])
                continue

            if len(current) + len(token) <= max_chars:
                current += token
                continue

            if current:
                pieces.append(current)
            current = token

        if current:
            pieces.append(current)

        return pieces


class AudioStitcher:
    """Join audio chunks together with a light crossfade."""

    CROSSFADE_MS = 30

    @classmethod
    def stitch(cls, audio_chunks: list[AudioSegment]) -> AudioSegment:
        """Return one continuous segment from one or more generated chunks."""

        if not audio_chunks:
            raise ValueError("No audio chunks to stitch")
        if len(audio_chunks) == 1:
            return audio_chunks[0]

        logger.info("Stitching %s audio chunks", len(audio_chunks))
        result = audio_chunks[0]
        for chunk in audio_chunks[1:]:
            crossfade = min(cls.CROSSFADE_MS, len(result), len(chunk))
            result = result.append(chunk, crossfade=crossfade)
        return result
