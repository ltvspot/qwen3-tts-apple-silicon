"""Regression coverage for text cleaner normalization."""

from __future__ import annotations

import pytest

from src.parser.text_cleaner import (
    TextCleaner,
    _expand_roman_numerals,
    _is_tts_artifact,
    _strip_inline_footnotes,
    merge_broken_paragraphs,
)


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


def test_merge_broken_paragraphs_ends_with_em_dash() -> None:
    """Paragraph ending with an em dash should merge with the next."""

    paragraphs = [
        "He leaned forward and whispered—",
        "then vanished into the crowd.",
    ]

    assert merge_broken_paragraphs(paragraphs) == [
        "He leaned forward and whispered— then vanished into the crowd."
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


def test_merge_broken_paragraphs_preserves_multi_em_dash_attribution() -> None:
    """Multi-em-dash attribution lines should not merge into following body text."""

    paragraphs = [
        "——— Helen King and Edward Mason",
        "in the middle of the night the dogs began to bark.",
    ]

    assert merge_broken_paragraphs(paragraphs) == paragraphs


def test_merge_broken_paragraphs_preserves_terminal_closing_bracket() -> None:
    """Paragraphs ending with a closing bracket should stay separate."""

    paragraphs = [
        "The phrase appears in the text (as written)",
        "and deserves a closer reading.",
    ]

    assert merge_broken_paragraphs(paragraphs) == paragraphs


def test_merge_broken_paragraphs_preserves_terminal_ellipsis() -> None:
    """Paragraphs ending with a Unicode ellipsis should stay separate."""

    paragraphs = [
        "And the poet kept speaking through the silence until the mountains answered…",
        "then the valley swallowed his voice.",
    ]

    assert merge_broken_paragraphs(paragraphs) == paragraphs


def test_merge_broken_paragraphs_preserves_nested_terminal_closing_brackets() -> None:
    """Stacked trailing brackets should still count as terminal."""

    paragraphs = [
        "The strategist returned to the commentary of the old masters]]",
        "then the soldiers resumed their march.",
    ]

    assert merge_broken_paragraphs(paragraphs) == paragraphs


@pytest.mark.parametrize("quote", ["“", "‘"])
def test_merge_broken_paragraphs_preserves_sentence_enders_before_left_quotes(quote: str) -> None:
    """Paragraphs ending in sentence punctuation plus a left quote should stay separate."""

    paragraphs = [
        f"The guide finished the story and stepped back into silence.{quote}",
        "then the hall went still.",
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


def test_merge_broken_paragraphs_expanded_non_terminal_word_list() -> None:
    """Expanded non-terminal words should merge even before uppercase proper nouns."""

    paragraphs = [
        "The central dispute had always been about",
        "Nietzsche's refusal to accept inherited morality.",
    ]

    assert merge_broken_paragraphs(paragraphs) == [
        "The central dispute had always been about Nietzsche's refusal to accept inherited morality."
    ]


def test_merge_broken_paragraphs_lowercase_continuation_after_arbitrary_word() -> None:
    """Long prose paragraphs that continue with lowercase text should merge."""

    paragraphs = [
        (
            "And could it really be believed that it finally seems to us as though "
            "the problem had never been raised before, as though we were the first"
        ),
        "to see it, to notice it, and to DARE raise it?",
    ]

    assert merge_broken_paragraphs(paragraphs) == [
        (
            "And could it really be believed that it finally seems to us as though "
            "the problem had never been raised before, as though we were the first "
            "to see it, to notice it, and to DARE raise it?"
        )
    ]


def test_merge_broken_paragraphs_preserves_short_heading_before_lowercase_body() -> None:
    """Short heading lines should stay separate even if the body starts lowercase."""

    paragraphs = [
        "Origins and Early Life",
        "he was born into a world of collapsing empires and old certainties.",
    ]

    assert merge_broken_paragraphs(paragraphs) == paragraphs


def test_merge_broken_paragraphs_preserves_very_long_paragraph_blocks() -> None:
    """Huge prose blocks without punctuation should not be treated as wrapped visual lines."""

    repeated = " ".join(["strategy"] * 120)
    paragraphs = [repeated, repeated]

    assert merge_broken_paragraphs(paragraphs) == paragraphs


def test_merge_broken_paragraphs_empty_paragraphs_skipped() -> None:
    """Empty input entries should be ignored."""

    paragraphs = ["First sentence.", "", "Second sentence.", ""]

    assert merge_broken_paragraphs(paragraphs) == ["First sentence.", "Second sentence."]


def test_merge_broken_paragraphs_strips_bare_number_artifacts() -> None:
    """Bare section numbers should be removed before merge decisions."""

    assert merge_broken_paragraphs(["1", "In the middle of the night..."]) == [
        "In the middle of the night..."
    ]


@pytest.mark.parametrize("marker", ["I", "II", "IV", "XIV", "XV", "MMXXV"])
def test_merge_broken_paragraphs_strips_bare_roman_artifacts(marker: str) -> None:
    """Standalone uppercase Roman stanza markers should be removed."""

    assert merge_broken_paragraphs([marker, "The snow still drifted through the dark."]) == [
        "The snow still drifted through the dark."
    ]


def test_merge_broken_paragraphs_strips_bare_roman_artifacts_with_period() -> None:
    """Standalone Roman markers with trailing punctuation should also be removed."""

    assert merge_broken_paragraphs(["XIV.", "The snow still drifted through the dark."]) == [
        "The snow still drifted through the dark."
    ]


def test_merge_broken_paragraphs_strips_asterisk_separator_artifacts() -> None:
    """Asterisk-only separator lines should be dropped."""

    assert merge_broken_paragraphs(["* * * * *", "It was September 3rd."]) == ["It was September 3rd."]


def test_merge_broken_paragraphs_strips_ascii_art_artifacts() -> None:
    """ASCII-art divider lines should be removed before merge decisions."""

    assert merge_broken_paragraphs(["____", "/ \\", "He was explaining..."]) == ["He was explaining..."]


def test_is_tts_artifact_preserves_numbered_sentences() -> None:
    """Numbered prose should not be discarded as artifacts."""

    assert _is_tts_artifact("1.") is False


def test_is_tts_artifact_strips_standalone_pua_glyphs() -> None:
    """Private Use Area glyph-only lines should be dropped before TTS."""

    assert _is_tts_artifact("\uf09a") is True


def test_is_tts_artifact_preserves_numbers_with_words() -> None:
    """Lines with real text after a number should remain."""

    assert _is_tts_artifact("12 people") is False


def test_is_tts_artifact_preserves_lowercase_roman_lines() -> None:
    """Only uppercase standalone Roman markers should be stripped."""

    assert _is_tts_artifact("xiv") is False


def test_is_tts_artifact_preserves_long_roman_like_strings() -> None:
    """Length guard should avoid over-matching suspicious all-Roman strings."""

    assert _is_tts_artifact("MMXVIIIX") is False


def test_strip_inline_footnotes_removes_curly_markers_and_trailing_dot_star() -> None:
    """Curly-brace footnotes and trailing dot-star markers should be removed."""

    assert _strip_inline_footnotes("Tom {13} turned back.*") == "Tom turned back"


def test_strip_inline_footnotes_removes_glued_superscript_digits() -> None:
    """Digits glued to the end of prose words should be stripped."""

    assert _strip_inline_footnotes("Imagination8 was his gift.") == "Imagination was his gift."


def test_merge_broken_paragraphs_strips_footnote_only_paragraphs_before_artifact_checks() -> None:
    """Footnote-only paragraphs should vanish before artifact filtering and merge logic."""

    assert merge_broken_paragraphs(["{5}", "Tom was about to blurt out something important."]) == [
        "Tom was about to blurt out something important."
    ]
