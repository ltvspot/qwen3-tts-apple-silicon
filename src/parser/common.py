"""Shared parser helpers for manuscript format adapters."""

from __future__ import annotations

import re
from dataclasses import dataclass


INTRODUCTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^(?P<label>introduction|preface|prologue)\b(?:\s*[:.\-]\s*(?P<title>.*))?$", re.IGNORECASE),
)

CHAPTER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"^chapter\s+(?P<number>[ivxlcdm]+|\d+|[a-z][a-z-]*)\s*[:.\-\u2013\u2014]?\s*(?P<title>.*)$",
        re.IGNORECASE,
    ),
    re.compile(r"^part\s+(?P<number>[ivxlcdm]+|\d+)\s*[:.\-]?\s*(?P<title>.*)$", re.IGNORECASE),
    re.compile(r"^(?P<number>[ivxlcdm]+|\d+)\s*[:.\-]\s*(?P<title>.+)$", re.IGNORECASE),
)

FRONT_MATTER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^title page\b", re.IGNORECASE),
    re.compile(r"^(copyright|©)\b", re.IGNORECASE),
    re.compile(r"^(table of contents|contents|toc)\b", re.IGNORECASE),
    re.compile(r"^preface(?:\s*[—-]\s*|\s+)message to the reader\b", re.IGNORECASE),
    re.compile(r"^message to the reader\b", re.IGNORECASE),
    re.compile(r"^foreword\b", re.IGNORECASE),
)

BACK_MATTER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^thank(?:s| you)?\s+you\s+for\s+reading\b", re.IGNORECASE),
    re.compile(r"^thank\s+for\s+reading\b", re.IGNORECASE),
    re.compile(r"^afterword\b", re.IGNORECASE),
    re.compile(r"^epilogue\b", re.IGNORECASE),
)

WORD_NUMBER_MAP: dict[str, int] = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
}
ESTIMATED_NARRATION_WORDS_PER_MINUTE = 150
AUTO_SPLIT_ESTIMATED_MINUTES = 110.0


@dataclass(slots=True, frozen=True)
class ParsedHeading:
    """Normalized heading details used across fallback parsers."""

    number: int
    title: str
    type: str


@dataclass(slots=True, frozen=True)
class ParagraphSplitResult:
    """One paragraph-aware split decision for a chapter body."""

    left_text: str
    right_text: str
    paragraph_index: int
    left_word_count: int
    right_word_count: int


def normalize_text(text: str) -> str:
    """Collapse internal whitespace while preserving line boundaries."""

    return " ".join(text.replace("\xa0", " ").split())


def normalize_heading_for_skip_rules(text: str) -> str:
    """Normalize heading text for punctuation-insensitive skip-rule matching."""

    normalized = normalize_text(text).casefold()
    normalized = re.sub(r"[^\w\s]", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def normalize_multiline_text(lines: list[str]) -> str:
    """Normalize a sequence of body lines into paragraph-safe text."""

    normalized_lines: list[str] = []
    for line in lines:
        normalized_line = normalize_text(line)
        if normalized_line:
            normalized_lines.append(normalized_line)
    if not normalized_lines:
        return ""
    text = "\n\n".join(normalized_lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def count_words(text: str) -> int:
    """Count words using the same loose tokenization as the DOCX parser."""

    return len(re.findall(r"\b[\w']+\b", text))


def estimate_duration_minutes(word_count: int | None, *, words_per_minute: int = ESTIMATED_NARRATION_WORDS_PER_MINUTE) -> float:
    """Estimate narration duration for a chapter body using a stable spoken-word WPM."""

    safe_word_count = max(int(word_count or 0), 0)
    return safe_word_count / max(words_per_minute, 1)


def split_into_paragraphs(text: str) -> list[str]:
    """Return the non-empty paragraphs contained in a narration body."""

    paragraphs = re.split(r"\n\s*\n", text)
    return [normalize_text(paragraph) for paragraph in paragraphs if normalize_text(paragraph)]


def split_text_at_paragraph(
    text: str,
    *,
    paragraph_index: int | None = None,
) -> ParagraphSplitResult | None:
    """Split chapter text on a paragraph boundary, defaulting to the midpoint by word count."""

    paragraphs = split_into_paragraphs(text)
    if len(paragraphs) < 2:
        return None

    word_counts = [count_words(paragraph) for paragraph in paragraphs]
    if paragraph_index is None:
        total_words = sum(word_counts)
        if total_words <= 0:
            return None
        target_words = total_words / 2.0
        running_words = 0
        best_index = 1
        best_delta = float("inf")
        for candidate_index in range(1, len(paragraphs)):
            running_words += word_counts[candidate_index - 1]
            delta = abs(running_words - target_words)
            if delta < best_delta:
                best_delta = delta
                best_index = candidate_index
        split_index = best_index
    else:
        split_index = paragraph_index + 1
        if split_index <= 0 or split_index >= len(paragraphs):
            raise ValueError(
                f"paragraph_index must reference an internal paragraph boundary between 0 and {len(paragraphs) - 2}."
            )

    left_text = normalize_multiline_text(paragraphs[:split_index])
    right_text = normalize_multiline_text(paragraphs[split_index:])
    if not left_text or not right_text:
        return None

    return ParagraphSplitResult(
        left_text=left_text,
        right_text=right_text,
        paragraph_index=split_index - 1,
        left_word_count=count_words(left_text),
        right_word_count=count_words(right_text),
    )


def is_front_matter_heading(text: str) -> bool:
    """Return whether text is non-narrated front matter."""

    normalized_text = normalize_heading_for_skip_rules(text)
    return any(pattern.match(normalized_text) for pattern in FRONT_MATTER_PATTERNS)


def is_back_matter_heading(text: str) -> bool:
    """Return whether text marks terminal back matter."""

    normalized_text = normalize_heading_for_skip_rules(text)
    return any(pattern.match(normalized_text) for pattern in BACK_MATTER_PATTERNS)


def should_skip_heading(text: str) -> bool:
    """Return whether a heading should never be narrated."""

    return is_front_matter_heading(text) or is_back_matter_heading(text)


def classify_heading(text: str) -> ParsedHeading | None:
    """Classify a heading as introduction or numbered chapter when possible."""

    normalized_text = normalize_text(text)
    if not normalized_text or should_skip_heading(normalized_text):
        return None

    for pattern in INTRODUCTION_PATTERNS:
        match = pattern.match(normalized_text)
        if match:
            label = normalize_text(match.group("label"))
            return ParsedHeading(number=0, title=label.title(), type="introduction")

    for pattern in CHAPTER_PATTERNS:
        match = pattern.match(normalized_text)
        if not match:
            continue
        chapter_number = coerce_chapter_number(match.group("number"))
        if chapter_number is None:
            continue
        title = normalize_text(match.group("title"))
        if pattern is CHAPTER_PATTERNS[2] and not title:
            continue
        return ParsedHeading(
            number=chapter_number,
            title=title or f"Chapter {chapter_number}",
            type="chapter",
        )

    return None


def coerce_chapter_number(value: str) -> int | None:
    """Convert a chapter number token into an integer when possible."""

    normalized_value = value.strip().strip(".:").replace("-", " ").casefold()
    if normalized_value.isdigit():
        return int(normalized_value)
    if re.fullmatch(r"[ivxlcdm]+", normalized_value, re.IGNORECASE):
        return roman_to_int(normalized_value.upper())
    if normalized_value in WORD_NUMBER_MAP:
        return WORD_NUMBER_MAP[normalized_value]
    return None


def roman_to_int(value: str) -> int:
    """Convert a Roman numeral string into an integer."""

    numerals = {
        "I": 1,
        "V": 5,
        "X": 10,
        "L": 50,
        "C": 100,
        "D": 500,
        "M": 1000,
    }
    total = 0
    previous_value = 0
    for character in reversed(value):
        numeral_value = numerals[character]
        if numeral_value < previous_value:
            total -= numeral_value
        else:
            total += numeral_value
            previous_value = numeral_value
    return total
