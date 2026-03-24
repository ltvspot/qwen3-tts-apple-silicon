"""Helpers for text chunking and audio stitching."""

from __future__ import annotations

import logging
import re

from pydub.audio_segment import AudioSegment

logger = logging.getLogger(__name__)


class TextChunker:
    """Split long narration text into chunk-safe segments."""

    _SENTENCE_PATTERN = re.compile(r".+?(?:[.!?](?:['\")\]]*)?(?:\s+|$)|$)", re.DOTALL)

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

        for segment in cls._split_sentences(text):
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
    def _split_sentences(cls, text: str) -> list[str]:
        """Split text into sentence-like segments while preserving whitespace."""

        sentences = [match.group(0) for match in cls._SENTENCE_PATTERN.finditer(text) if match.group(0)]
        return sentences or [text]

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
