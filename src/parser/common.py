"""Shared parser helpers for manuscript format adapters."""

from __future__ import annotations

import re
from dataclasses import dataclass


INTRODUCTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^(?P<label>introduction|preface|prologue)\b(?:\s*[:.\-]\s*(?P<title>.*))?$", re.IGNORECASE),
)

CHAPTER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"^chapter\s+(?P<number>[ivxlcdm]+|\d+|[a-z][a-z-]*)\s*[:.\-]?\s*(?P<title>.*)$",
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


@dataclass(slots=True, frozen=True)
class ParsedHeading:
    """Normalized heading details used across fallback parsers."""

    number: int
    title: str
    type: str


def normalize_text(text: str) -> str:
    """Collapse internal whitespace while preserving line boundaries."""

    return " ".join(text.replace("\xa0", " ").split())


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


def is_front_matter_heading(text: str) -> bool:
    """Return whether text is non-narrated front matter."""

    normalized_text = normalize_text(text)
    return any(pattern.match(normalized_text) for pattern in FRONT_MATTER_PATTERNS)


def is_back_matter_heading(text: str) -> bool:
    """Return whether text marks terminal back matter."""

    normalized_text = normalize_text(text)
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
