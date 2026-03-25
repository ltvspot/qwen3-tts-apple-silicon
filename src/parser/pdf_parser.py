"""PDF manuscript parsing for audiobook chapter extraction."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import pdfplumber

from src.parser.common import (
    ParsedHeading,
    classify_heading,
    count_words,
    is_back_matter_heading,
    is_front_matter_heading,
    normalize_multiline_text,
    normalize_text,
)
from src.parser.docx_parser import BookMetadata, Chapter

logger = logging.getLogger(__name__)


class PDFParser:
    """Parse text-based PDF manuscripts into metadata and narratable chapters."""

    def parse(self, pdf_path: str | Path) -> tuple[BookMetadata, list[Chapter]]:
        """
        Parse a PDF file and return extracted metadata plus chapter content.

        Args:
            pdf_path: Path to the PDF manuscript.

        Returns:
            A tuple of book metadata and detected chapters.

        Raises:
            ValueError: If the file is missing, unreadable, or missing narratable content.
        """

        path = Path(pdf_path)
        if not path.exists():
            raise ValueError(f"PDF file does not exist: {path}")

        logger.info("Parsing PDF manuscript: %s", path)

        try:
            with pdfplumber.open(str(path)) as pdf:
                metadata = self._extract_metadata(pdf)
                chapters = self._extract_chapters(pdf)
        except Exception as exc:
            raise ValueError(f"Failed to read PDF file: {path}") from exc

        if not chapters:
            raise ValueError(f"No narratable chapters detected in {path}")

        logger.info("Extracted %s narratable chapters from %s", len(chapters), path)
        return metadata, chapters

    def _extract_metadata(self, pdf: pdfplumber.PDF) -> BookMetadata:
        """Extract metadata from the PDF info dictionary or first-page text."""

        info = pdf.metadata or {}
        title = self._coerce_metadata_value(info.get("Title") or info.get("/Title"))
        author = self._coerce_metadata_value(info.get("Author") or info.get("/Author"))
        subtitle = self._coerce_metadata_value(info.get("Subject") or info.get("/Subject"))

        if title and author:
            return BookMetadata(title=title, subtitle=subtitle, author=author, original_publisher=None)

        first_page_lines = self._extract_first_page_lines(pdf)
        if not title and first_page_lines:
            title = first_page_lines[0]
        if not author:
            author = self._detect_author_line(first_page_lines)

        return BookMetadata(
            title=title or "Unknown",
            subtitle=subtitle,
            author=author or "Unknown",
            original_publisher=None,
        )

    def _extract_chapters(self, pdf: pdfplumber.PDF) -> list[Chapter]:
        """Split extracted PDF text into chapter sections using heading heuristics."""

        chapters: list[Chapter] = []
        current_heading: ParsedHeading | None = None
        current_body: list[str] = []
        narration_started = False

        for page_number, page in enumerate(pdf.pages, start=1):
            try:
                page_text = page.extract_text() or ""
            except Exception as exc:
                logger.warning("Failed to extract text from page %s: %s", page_number, exc)
                continue

            page_lines = [normalize_text(raw_line) for raw_line in page_text.splitlines() if normalize_text(raw_line)]
            if not narration_started and self._is_toc_page(page_lines):
                continue

            for line in page_lines:
                parsed_heading = classify_heading(line)

                if not narration_started:
                    if parsed_heading is None:
                        continue
                    narration_started = True
                    current_heading = parsed_heading
                    current_body = []
                    continue

                if is_back_matter_heading(line):
                    built = self._build_chapter(current_heading, current_body)
                    if built is not None:
                        chapters.append(built)
                    return chapters

                if is_front_matter_heading(line):
                    continue

                if parsed_heading is not None:
                    if parsed_heading.type == "introduction" and current_heading is not None:
                        current_body.append(line)
                        continue

                    built = self._build_chapter(current_heading, current_body)
                    if built is not None:
                        chapters.append(built)
                    current_heading = parsed_heading
                    current_body = []
                    continue

                current_body.append(line)

        built = self._build_chapter(current_heading, current_body)
        if built is not None:
            chapters.append(built)
        return chapters

    def _is_toc_page(self, lines: list[str]) -> bool:
        """Return whether a page looks like a table-of-contents page."""

        return any(re.match(r"^(table of contents|contents|toc)\b", line, re.IGNORECASE) for line in lines)

    def _build_chapter(self, heading: ParsedHeading | None, body_lines: list[str]) -> Chapter | None:
        """Construct a chapter object when a heading has accumulated readable body text."""

        if heading is None:
            return None

        text = normalize_multiline_text(body_lines)
        if not text:
            return None

        return Chapter(
            number=heading.number,
            title=heading.title,
            type=heading.type,
            raw_text=text,
            word_count=count_words(text),
        )

    def _extract_first_page_lines(self, pdf: pdfplumber.PDF) -> list[str]:
        """Return normalized non-empty lines from the first readable page."""

        for page in pdf.pages[:3]:
            text = page.extract_text() or ""
            lines = [normalize_text(line) for line in text.splitlines() if normalize_text(line)]
            if lines:
                return lines
        return []

    def _detect_author_line(self, lines: list[str]) -> str | None:
        """Infer the author line from the first-page text when metadata is absent."""

        for line in lines[1:6]:
            match = re.match(r"^(?:by|written by)\s+(?P<author>.+)$", line, re.IGNORECASE)
            if match:
                return normalize_text(match.group("author"))
        return None

    def _coerce_metadata_value(self, value: Any) -> str | None:
        """Normalize metadata dictionary values into plain strings."""

        if value is None:
            return None
        text = normalize_text(str(value))
        return text or None
