"""Regression coverage for text cleaner normalization."""

from __future__ import annotations

import pytest

from src.parser.text_cleaner import TextCleaner, _expand_roman_numerals, merge_broken_paragraphs


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("I. Brief Biography\n", "One. Brief Biography\n"),
        ("ii. Methods\n", "Two. Methods\n"),
        ("IV. Attack By Stratagem\n", "Four. Attack By Stratagem\n"),
        ("IX. Strategic Advantages\n", "Nine. Strategic Advantages\n"),
        ("XIV. The Use of Spies\n", "Fourteen. The Use of Spies\n"),
        ("XXI. Variations\n", "Twenty-One. Variations\n"),
        ("XXV. Final Notes\n", "Twenty-Five. Final Notes\n"),
        ("XXX. Thirty\n", "Thirty. Thirty\n"),
        ("XL. Forty\n", "Forty. Forty\n"),
        ("L. Fifty\n", "Fifty. Fifty\n"),
    ],
)
def test_expand_roman_numerals_heading_cases(source: str, expected: str) -> None:
    """Roman numeral headings from I through L should expand to cardinal words."""

    assert _expand_roman_numerals(source) == expected


def test_expand_roman_numerals_multiline() -> None:
    """Multiple Roman numeral headings should expand in one pass."""

    text = "I. Introduction\nII. Methods\nIII. Results\n"
    expected = "One. Introduction\nTwo. Methods\nThree. Results\n"

    assert _expand_roman_numerals(text) == expected


def test_expand_roman_numerals_parenthetical() -> None:
    """Parenthetical Roman numerals should expand in lowercase."""

    assert (
        _expand_roman_numerals("See section (iv) for details and compare (X).")
        == "See section (four) for details and compare (ten)."
    )


def test_expand_roman_numerals_avoids_false_positives() -> None:
    """Pronouns and normal letter usage should remain untouched."""

    text = (
        "I went to the store.\n"
        "When I was young, I read often.\n"
        "The letter V is a vowel in this example.\n"
        "Chapter V appears later."
    )

    assert _expand_roman_numerals(text) == text


def test_text_cleaner_pipeline_expands_roman_headings() -> None:
    """The main cleaning pipeline should expand Roman numeral headings for TTS."""

    cleaner = TextCleaner()

    assert cleaner.clean("I. Brief Biography of Sun Tzu\n(ii) Supporting note") == (
        "One. Brief Biography of Sun Tzu\n(two) Supporting note"
    )


def test_merge_broken_paragraphs_mid_sentence_preposition() -> None:
    """Paragraph ending with a non-terminal word should merge with the next."""

    paragraphs = [
        "It is the empty space in the center that makes the",
        "wheel useful.",
    ]

    assert merge_broken_paragraphs(paragraphs) == [
        "It is the empty space in the center that makes the wheel useful."
    ]


def test_merge_broken_paragraphs_ends_with_comma() -> None:
    """Paragraph ending with a comma should merge with the next."""

    paragraphs = [
        "as profound, bruising, and expansive as",
        "Pascal's intellectual conscience.",
    ]

    assert merge_broken_paragraphs(paragraphs) == [
        "as profound, bruising, and expansive as Pascal's intellectual conscience."
    ]


def test_merge_broken_paragraphs_preserves_complete_sentences() -> None:
    """Complete sentences should remain separate paragraphs."""

    paragraphs = [
        "The Tao that can be named is not the eternal Tao.",
        "The name that can be named is not the eternal name.",
    ]

    assert merge_broken_paragraphs(paragraphs) == paragraphs


def test_merge_broken_paragraphs_preserves_headings() -> None:
    """Heading-like lines should not merge with following body text."""

    paragraphs = [
        "Simplicity, Self-Reliance, and the Soul of America",
        "Walden is often celebrated as a masterpiece of American individualism.",
    ]

    assert merge_broken_paragraphs(paragraphs) == paragraphs


def test_merge_broken_paragraphs_chain() -> None:
    """Multiple consecutive broken paragraphs should merge into one sentence."""

    paragraphs = [
        "The natural flow of the universe cannot be",
        "controlled, for it is",
        "beyond all effort.",
    ]

    assert merge_broken_paragraphs(paragraphs) == [
        "The natural flow of the universe cannot be controlled, for it is beyond all effort."
    ]


def test_merge_broken_paragraphs_empty_paragraphs_skipped() -> None:
    """Empty input entries should be ignored."""

    paragraphs = ["First sentence.", "", "Second sentence.", ""]

    assert merge_broken_paragraphs(paragraphs) == ["First sentence.", "Second sentence."]
