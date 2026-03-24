"""Text cleaning helpers for TTS preparation."""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


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
        text = self._normalize_dashes(text)
        text = self._normalize_ellipsis(text)
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
        """Normalize dash variants to a single em dash."""

        for variant in self.em_dash_variants:
            text = text.replace(variant, "—")
        return text

    def _normalize_ellipsis(self, text: str) -> str:
        """Normalize ellipsis characters for broad TTS compatibility."""

        return text.replace(self.ellipsis_variant, "...")

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
