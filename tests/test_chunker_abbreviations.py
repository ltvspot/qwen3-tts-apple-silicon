"""Tests for sentence splitting around abbreviations and decimals."""

from __future__ import annotations

import unicodedata

from src.engines.chunker import TextChunker


def test_chunker_dr_smith() -> None:
    """Honorific abbreviations should not split a sentence."""

    sentences = TextChunker.split_into_sentences("Dr. Smith went to 3.14 Baker St.")

    assert sentences == ["Dr. Smith went to 3.14 Baker St."]


def test_chunker_decimal() -> None:
    """Decimal numbers should stay inside the same sentence."""

    sentences = TextChunker.split_into_sentences("The cost is $99.99. Order now.")

    assert sentences == ["The cost is $99.99. ", "Order now."]


def test_chunker_initials() -> None:
    """Initials should not create extra sentence boundaries."""

    sentences = TextChunker.split_into_sentences("J. K. Rowling wrote it.")

    assert sentences == ["J. K. Rowling wrote it."]


def test_chunker_normal_split() -> None:
    """Normal sentence punctuation should still split as expected."""

    sentences = TextChunker.split_into_sentences("Mr. Jones said hello. She waved back.")

    assert sentences == ["Mr. Jones said hello. ", "She waved back."]


def test_chunker_preserves_combining_accents_when_chunking() -> None:
    """Oversized fallback splitting should not leave decomposed accents behind."""

    chunks = TextChunker.chunk_text("Cafe\u0301 re\u0301sume\u0301", max_chars=4)

    assert unicodedata.normalize("NFC", "".join(chunks)) == "Café résumé"
    assert all(unicodedata.is_normalized("NFC", chunk) for chunk in chunks)
