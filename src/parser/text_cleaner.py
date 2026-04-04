"""Text cleaning helpers for TTS preparation."""

from __future__ import annotations

import logging
import re

from .common import roman_to_int

logger = logging.getLogger(__name__)

_ROMAN_HEADING_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<roman>[ivxl]+)\.(?=\s)",
    re.MULTILINE | re.IGNORECASE,
)
_ROMAN_PAREN_RE = re.compile(r"(?<!\w)\((?P<roman>[ivxl]+)\)(?!\w)", re.IGNORECASE)
_CANONICAL_ROMAN_RE = re.compile(
    r"^(?:XL|L|X{0,3})(?:IX|IV|V?I{0,3})$",
    re.IGNORECASE,
)
_CARDINAL_UNITS: dict[int, str] = {
    0: "Zero",
    1: "One",
    2: "Two",
    3: "Three",
    4: "Four",
    5: "Five",
    6: "Six",
    7: "Seven",
    8: "Eight",
    9: "Nine",
    10: "Ten",
    11: "Eleven",
    12: "Twelve",
    13: "Thirteen",
    14: "Fourteen",
    15: "Fifteen",
    16: "Sixteen",
    17: "Seventeen",
    18: "Eighteen",
    19: "Nineteen",
}
_CARDINAL_TENS: dict[int, str] = {
    20: "Twenty",
    30: "Thirty",
    40: "Forty",
    50: "Fifty",
}
_NON_TERMINAL_WORDS: frozenset[str] = frozenset(
    [
        "a",
        "about",
        "above",
        "across",
        "after",
        "against",
        "all",
        "along",
        "alongside",
        "although",
        "amid",
        "among",
        "amongst",
        "am",
        "an",
        "and",
        "another",
        "any",
        "are",
        "around",
        "as",
        "at",
        "be",
        "because",
        "been",
        "before",
        "being",
        "behind",
        "below",
        "beneath",
        "beside",
        "besides",
        "between",
        "beyond",
        "both",
        "but",
        "by",
        "can",
        "could",
        "despite",
        "did",
        "do",
        "does",
        "down",
        "during",
        "each",
        "either",
        "enough",
        "every",
        "except",
        "few",
        "for",
        "from",
        "had",
        "has",
        "have",
        "he",
        "her",
        "his",
        "if",
        "in",
        "inside",
        "into",
        "is",
        "it",
        "its",
        "like",
        "many",
        "may",
        "might",
        "more",
        "most",
        "much",
        "must",
        "my",
        "near",
        "neither",
        "no",
        "nor",
        "not",
        "of",
        "off",
        "on",
        "once",
        "onto",
        "or",
        "other",
        "our",
        "out",
        "outside",
        "over",
        "past",
        "rather",
        "several",
        "shall",
        "she",
        "should",
        "since",
        "so",
        "some",
        "such",
        "than",
        "that",
        "the",
        "their",
        "them",
        "these",
        "they",
        "this",
        "those",
        "though",
        "through",
        "throughout",
        "to",
        "toward",
        "towards",
        "under",
        "underneath",
        "unless",
        "until",
        "up",
        "upon",
        "via",
        "was",
        "we",
        "were",
        "what",
        "whatever",
        "when",
        "whenever",
        "where",
        "whereas",
        "wherever",
        "whether",
        "which",
        "whichever",
        "while",
        "who",
        "whoever",
        "whom",
        "whose",
        "will",
        "with",
        "within",
        "without",
        "would",
        "yet",
        "you",
        "your",
    ]
)
_EM_DASH_ENDERS: frozenset[str] = frozenset({"—", "–", "─", "−"})
_TERMINAL_PUNCTUATION: frozenset[str] = frozenset({".", "!", "?", ";", ":"})
_CLOSING_TERMINATORS: frozenset[str] = frozenset({'"', "'", ")", "]", "}", "”", "’"})
_SENTENCE_ENDERS: frozenset[str] = frozenset(_TERMINAL_PUNCTUATION | _EM_DASH_ENDERS | {"“", "‘", "…"})
_ATTRIBUTION_RE = re.compile(r'^[~—–]{1,4}\s*\w|^\*{1,2}[^*]+\*{1,2}$')
_TRAILING_WORD_RE = re.compile(r"([A-Za-z]+(?:['’][A-Za-z]+)?)\s*$")
_WORD_RE = re.compile(r"\b[\w]+(?:['’][\w]+)?\b")
_LEADING_CONTINUATION_CHARS = "\"'“‘(["
_MIN_LOWERCASE_CONTINUATION_WORDS = 5
_MIN_LOWERCASE_CONTINUATION_PARAGRAPH_CHARS = 50
_MAX_LOWERCASE_CONTINUATION_WORDS = 80
_BARE_NUMBER_RE = re.compile(r"^\d{1,3}$")
_BARE_ROMAN_RE = re.compile(r"^[IVXLCDM]+\.?\s*$")
_ASTERISK_SEP_RE = re.compile(r"^\*[\s\*]+\*$")
_ASCII_ART_RE = re.compile(r"^(?=.*[_\-\*/\\|=])[_\-\*/\\|=\s]{3,}$")
_PUA_RE = re.compile(r"^[\ue000-\uf8ff\s]+$")
_CURLY_BRACE_FOOTNOTE_RE = re.compile(r"\{\d+\}")
_TRAILING_DOT_ASTERISK_RE = re.compile(r"(?:\s*\.\*)+\s*$")
_GLUED_FOOTNOTE_DIGIT_RE = re.compile(
    r"(\b[A-Za-z]+(?:[-'’][A-Za-z]+)*)"
    r"([0-9\u00b2\u00b3\u00b9\u2070-\u2079]{1,3})(?=(?:\s|[\"'”’)\].,;:!?]|$))"
)


def _cardinal_word(number: int) -> str | None:
    """Return the spoken cardinal word for a supported heading number."""

    if number < 1 or number > 50:
        return None
    if number < 20:
        return _CARDINAL_UNITS[number]

    tens = (number // 10) * 10
    ones = number % 10
    tens_word = _CARDINAL_TENS.get(tens)
    if tens_word is None:
        return None
    if ones == 0:
        return tens_word
    return f"{tens_word}-{_CARDINAL_UNITS[ones]}"


def _roman_to_spoken_word(value: str, *, lowercase: bool = False) -> str | None:
    """Convert a conservative Roman numeral subset into a spoken cardinal word."""

    normalized_value = value.upper()
    if not _CANONICAL_ROMAN_RE.fullmatch(normalized_value):
        return None

    number = roman_to_int(normalized_value)
    word = _cardinal_word(number)
    if word is None:
        return None
    return word.lower() if lowercase else word


def _expand_roman_numerals(text: str) -> str:
    """Convert Roman numeral headings and parenthetical labels into spoken words."""

    def _replace_heading(match: re.Match[str]) -> str:
        word = _roman_to_spoken_word(match.group("roman"))
        if word is None:
            return match.group(0)
        return f"{match.group('indent')}{word}."

    def _replace_parenthetical(match: re.Match[str]) -> str:
        word = _roman_to_spoken_word(match.group("roman"), lowercase=True)
        if word is None:
            return match.group(0)
        return f"({word})"

    text = _ROMAN_HEADING_RE.sub(_replace_heading, text)
    text = _ROMAN_PAREN_RE.sub(_replace_parenthetical, text)
    return text


def _starts_with_lowercase_continuation(para: str) -> bool:
    """Return True when a paragraph visibly starts with lowercase continuation text."""

    stripped = para.lstrip()
    while stripped and stripped[0] in _LEADING_CONTINUATION_CHARS:
        stripped = stripped[1:].lstrip()
    if not stripped:
        return False
    return stripped[0].islower()


def _strip_trailing_closers(text: str) -> str:
    """Remove trailing quotes/brackets before inspecting sentence-ending punctuation."""

    stripped = text.rstrip()
    while stripped and stripped[-1] in _CLOSING_TERMINATORS:
        stripped = stripped[:-1].rstrip()
    return stripped


def _is_prose_like_paragraph(para: str) -> bool:
    """Return True for paragraphs long enough to safely treat as body prose."""

    return (
        len(_WORD_RE.findall(para)) >= _MIN_LOWERCASE_CONTINUATION_WORDS
        or len(para) >= _MIN_LOWERCASE_CONTINUATION_PARAGRAPH_CHARS
    )


def _is_line_wrap_sized_paragraph(para: str) -> bool:
    """Return True when a paragraph is short enough to plausibly be a wrapped prose line."""

    return len(_WORD_RE.findall(para)) <= _MAX_LOWERCASE_CONTINUATION_WORDS


def _is_attribution(para: str) -> bool:
    """Return True for standalone attribution/signature lines."""

    return bool(_ATTRIBUTION_RE.match(para.strip()))


def _is_terminal(para: str) -> bool:
    """Return True when a paragraph visibly ends a sentence or closed clause."""

    stripped = para.rstrip()
    if not stripped:
        return False

    saw_closer = False
    while stripped and stripped[-1] in _CLOSING_TERMINATORS:
        saw_closer = True
        stripped = stripped[:-1].rstrip()

    if saw_closer:
        return True
    if not stripped:
        return False
    return stripped[-1] in _SENTENCE_ENDERS


def _is_tts_artifact(para: str) -> bool:
    """Return True for structural extraction artifacts that should be dropped before TTS."""

    stripped = para.strip()
    if not stripped:
        return True
    if _PUA_RE.match(stripped):
        return True
    if _BARE_NUMBER_RE.match(stripped):
        return True
    if _BARE_ROMAN_RE.match(stripped) and len(stripped.rstrip(".").strip()) <= 7:
        return True
    if _ASTERISK_SEP_RE.match(stripped):
        return True
    if _ASCII_ART_RE.match(stripped):
        return True
    return False


def _strip_inline_footnotes(text: str) -> str:
    """Remove inline footnote markers that would otherwise be read aloud by TTS."""

    text = _CURLY_BRACE_FOOTNOTE_RE.sub("", text)
    text = _GLUED_FOOTNOTE_DIGIT_RE.sub(r"\1", text)
    text = _TRAILING_DOT_ASTERISK_RE.sub("", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def _is_mid_sentence_break(para: str, next_para: str) -> bool:
    """Return True if this paragraph appears to end mid-sentence.

    A paragraph is mid-sentence if:
    - It ends with an em dash interruption, OR
    - It ends with a comma, OR
    - A longer prose paragraph ends without terminal punctuation and the
      next paragraph starts with lowercase continuation text, OR
    - Its last word is a grammatical non-terminal in a prose-sized paragraph.

    We do not merge short heading-like phrases into following body text.
    """

    if not para or not next_para:
        return False

    stripped_para = para.rstrip()
    stripped_next = next_para.lstrip()
    if not stripped_para or not stripped_next:
        return False
    if _is_attribution(stripped_para):
        return False

    core_para = _strip_trailing_closers(stripped_para)
    if not core_para:
        return False

    last_char = core_para[-1]
    if last_char in _EM_DASH_ENDERS:
        return True
    if last_char == ",":
        return True
    if _is_terminal(stripped_para):
        return False

    if (
        _starts_with_lowercase_continuation(stripped_next)
        and _is_prose_like_paragraph(stripped_para)
        and _is_line_wrap_sized_paragraph(stripped_para)
    ):
        return True

    if not _is_prose_like_paragraph(stripped_para):
        return False

    match = _TRAILING_WORD_RE.search(core_para)
    if not match:
        return False

    return match.group(1).lower() in _NON_TERMINAL_WORDS


def merge_broken_paragraphs(paragraphs: list[str]) -> list[str]:
    """Merge consecutive paragraphs that form part of the same sentence.

    When a DOCX paragraph ends mid-sentence, the following paragraph is
    appended with a single space instead of being treated as a new paragraph.
    The detector handles explicit interruption punctuation first (em dashes,
    commas), refuses to merge after terminal punctuation or closing brackets,
    and uses lowercase continuation plus non-terminal word cues for prose.

    This preserves intentional paragraph breaks between complete sentences
    while healing broken sentences caused by Word line-wrap artefacts.

    Args:
        paragraphs: List of non-empty paragraph strings.

    Returns:
        New list with mid-sentence paragraphs merged.
    """

    paragraphs = [_strip_inline_footnotes(paragraph) for paragraph in paragraphs]
    paragraphs = [paragraph for paragraph in paragraphs if not _is_tts_artifact(paragraph.strip())]

    result: list[str] = []
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if result and _is_mid_sentence_break(result[-1], para):
            result[-1] = result[-1] + " " + para
        else:
            result.append(para)
    return result


class TextCleaner:
    """Clean manuscript text for TTS processing."""

    def __init__(self) -> None:
        """Initialize normalization rules."""

        self.abbreviation_map: tuple[tuple[re.Pattern[str], str], ...] = (
            (re.compile(r"\bDr\.\s+", re.IGNORECASE), "Doctor "),
            (re.compile(r"\bMr\.\s+", re.IGNORECASE), "Mister "),
            (re.compile(r"\bMrs\.\s+", re.IGNORECASE), "Missus "),
            (re.compile(r"\bMs\.\s+", re.IGNORECASE), "Ms "),
            (re.compile(r"\bProf\.\s+", re.IGNORECASE), "Professor "),
            (re.compile(r"\bRev\.\s+", re.IGNORECASE), "Reverend "),
            (re.compile(r"\bU\.S\.\s+", re.IGNORECASE), "U.S. "),
            (re.compile(r"\bU\.K\.\s+", re.IGNORECASE), "U.K. "),
        )
        self.em_dash_variants = ("—", "–", "─", "−")
        self.ellipsis_variant = "…"

    def clean(self, text: str) -> str:
        """Apply the full cleaning pipeline and return TTS-ready text."""

        text = self._remove_page_numbers(text)
        text = self._expand_abbreviations(text)
        text = _expand_roman_numerals(text)
        text = self._normalize_dashes(text)
        text = self._normalize_ellipsis(text)
        text = self._expand_common_patterns(text)
        text = self._strip_formatting_artifacts(text)
        text = self._normalize_whitespace(text)
        return text

    def _remove_page_numbers(self, text: str) -> str:
        """Remove standalone page number lines from extracted text."""

        cleaned_lines: list[str] = []
        patterns = (
            re.compile(r"^\d+$"),
            re.compile(r"^Page\s+\d+$", re.IGNORECASE),
            re.compile(r"^-+\s*\d+\s*-+$"),
        )

        for line in text.splitlines():
            stripped = line.strip()
            if stripped and any(pattern.match(stripped) for pattern in patterns):
                continue
            cleaned_lines.append(line)

        return "\n".join(cleaned_lines)

    def _expand_abbreviations(self, text: str) -> str:
        """Expand common abbreviations without flattening surrounding punctuation."""

        for pattern, replacement in self.abbreviation_map:
            text = pattern.sub(replacement, text)

        text = re.sub(r"\bSt\.\s+(?=[A-Z])", "Saint ", text)
        text = re.sub(r"\bSt\.(?=(?:\s*$)|(?:\s+[a-z])|[,.;!?])", "Street", text)
        return text

    def _normalize_dashes(self, text: str) -> str:
        """Normalize dash variants: en-dashes in ranges become 'to', others become em dash."""

        # First: convert en-dash ranges (e.g., "pages 12–15", "1914–1918") to "to"
        text = re.sub(r"(\d)\s*–\s*(\d)", r"\1 to \2", text)
        # Then: normalize remaining dashes to em dash
        for variant in self.em_dash_variants:
            text = text.replace(variant, "—")
        return text

    def _normalize_ellipsis(self, text: str) -> str:
        """Normalize ellipsis characters for broad TTS compatibility."""

        return text.replace(self.ellipsis_variant, "...")

    def _expand_common_patterns(self, text: str) -> str:
        """Expand patterns that TTS models commonly mispronounce."""

        # "e.g." and "i.e." — spoken as full phrases
        text = re.sub(r"\be\.g\.\s*", "for example, ", text)
        text = re.sub(r"\bi\.e\.\s*", "that is, ", text)
        # "etc." — spoken as "et cetera"
        text = re.sub(r"\betc\.\s*", "et cetera. ", text)
        # "vs." — spoken as "versus"
        text = re.sub(r"\bvs\.\s*", "versus ", text)
        # "cf." — spoken as "compare"
        text = re.sub(r"\bcf\.\s*", "compare ", text)
        # "approx." — spoken as "approximately"
        text = re.sub(r"\bapprox\.\s*", "approximately ", text)
        # Century references: "19th century" is fine, but "C." or "cent." may confuse
        text = re.sub(r"\bcent\.\s*", "century ", text)
        # "A.D." and "B.C." — keep as-is, TTS handles these
        # "no." → "number" when before a digit
        text = re.sub(r"\bNo\.\s*(\d)", r"Number \1", text)
        text = re.sub(r"\bno\.\s*(\d)", r"number \1", text)
        return text

    def _strip_formatting_artifacts(self, text: str) -> str:
        """Remove common formatting artifacts introduced during extraction."""

        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"\[sic\]", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\*{3,}", "**", text)
        return text

    def _normalize_whitespace(self, text: str) -> str:
        """Normalize spacing while preserving paragraph breaks."""

        text = text.replace("\r\n", "\n").replace("\r", "\n")
        normalized_lines: list[str] = []

        for line in text.split("\n"):
            stripped = re.sub(r"[ \t]{2,}", " ", line.strip())
            normalized_lines.append(stripped)

        normalized_text = "\n".join(normalized_lines)
        normalized_text = re.sub(r"\n{3,}", "\n\n", normalized_text)
        return normalized_text.strip()
