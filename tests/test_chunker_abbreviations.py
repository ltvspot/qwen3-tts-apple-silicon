"""Tests for sentence splitting around abbreviations and decimals."""

from __future__ import annotations

from src.engines.chunker import TextChunker


def test_chunker_dr_smith() -> None:
    """Honorific abbreviations should not split a sentence."""

    sentences = TextChunker.split_into_sentences("Dr. Smith went home.")

    assert sentences == ["Dr. Smith went home."]


def test_chunker_decimal() -> None:
    """Decimal numbers should stay inside the same sentence."""

    sentences = TextChunker.split_into_sentences("The value is 3.14.")

    assert sentences == ["The value is 3.14."]


def test_chunker_initials() -> None:
    """Initials should not create extra sentence boundaries."""

    sentences = TextChunker.split_into_sentences("J. K. Rowling wrote it.")

    assert sentences == ["J. K. Rowling wrote it."]


def test_chunker_normal_split() -> None:
    """Normal sentence punctuation should still split as expected."""

    sentences = TextChunker.split_into_sentences("Hello. Goodbye.")

    assert sentences == ["Hello. ", "Goodbye."]
