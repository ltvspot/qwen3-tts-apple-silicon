"""DOCX manuscript parsing for audiobook chapter extraction."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from docx import Document as load_document
from docx.document import Document as DocxDocument
from docx.opc.exceptions import PackageNotFoundError
from docx.text.paragraph import Paragraph

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class BookMetadata:
    """Extracted title page metadata."""

    title: str
    subtitle: str | None
    author: str
    original_publisher: str | None


@dataclass(slots=True)
class Chapter:
    """Represents a single narratable chapter."""

    number: int
    title: str
    type: str
    raw_text: str
    word_count: int


@dataclass(slots=True)
class _ParagraphInfo:
    """Normalized paragraph data used during parsing."""

    index: int
    text: str
    style: str | None
    paragraph: Paragraph


class DocxParser:
    """Parse DOCX manuscripts into metadata and narratable chapters."""

    def __init__(self) -> None:
        """Initialize chapter detection and skip rules."""

        self.chapter_patterns: tuple[re.Pattern[str], ...] = (
            re.compile(
                r"^chapter\s+(?P<number>[ivxlcdm]+|\d+|[a-z][a-z-]*)\s*[:.\-]?\s*(?P<title>.*)$",
                re.IGNORECASE,
            ),
            re.compile(r"^(?P<number>[ivxlcdm]+|\d+)\s*[:.\-]\s*(?P<title>.+)$", re.IGNORECASE),
        )
        self.intro_patterns: tuple[re.Pattern[str], ...] = (
            re.compile(
                r"^(?P<label>introduction|preface|prologue)\b(?:\s*[:.\-]\s*(?P<title>.*))?$",
                re.IGNORECASE,
            ),
        )
        self.front_matter_patterns: tuple[re.Pattern[str], ...] = (
            re.compile(r"^(copyright|©)\b", re.IGNORECASE),
            re.compile(r"^(table of contents|contents)\b", re.IGNORECASE),
            re.compile(r"^preface(?:\s*[—-]\s*|\s+)message to the reader\b", re.IGNORECASE),
        )
        self.back_matter_patterns: tuple[re.Pattern[str], ...] = (
            re.compile(r"^thank(?:s| you)?\s+you\s+for\s+reading\b", re.IGNORECASE),
            re.compile(r"^thank\s+for\s+reading\b", re.IGNORECASE),
            re.compile(r"^epilogue\b", re.IGNORECASE),
        )
        self.author_patterns: tuple[re.Pattern[str], ...] = (
            re.compile(r"^(?:by|written by)\s+(?P<author>.+)$", re.IGNORECASE),
        )
        self.publisher_patterns: tuple[re.Pattern[str], ...] = (
            re.compile(r"^(?:originally\s+published\s+by|published\s+by)\s+(?P<publisher>.+)$", re.IGNORECASE),
        )
        self.word_number_map: dict[str, int] = {
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
        self.last_toc_entries: list[str] = []

    def parse(self, docx_path: str | Path) -> tuple[BookMetadata, list[Chapter]]:
        """
        Parse a DOCX file and return extracted metadata plus chapter content.

        Args:
            docx_path: Path to the DOCX manuscript.

        Returns:
            A tuple of book metadata and detected chapters.

        Raises:
            ValueError: If the file is missing, unreadable, or missing critical metadata.
        """

        path = Path(docx_path)
        if not path.exists():
            raise ValueError(f"DOCX file does not exist: {path}")

        logger.info("Parsing DOCX manuscript: %s", path)

        try:
            document = load_document(str(path))
        except PackageNotFoundError as exc:
            raise ValueError(f"Invalid DOCX file: {path}") from exc
        except Exception as exc:
            raise ValueError(f"Failed to read DOCX file: {path}") from exc

        metadata = self._extract_metadata(document)
        chapters = self._extract_chapters(document)
        if not chapters:
            raise ValueError(f"No narratable chapters detected in {path}")

        logger.info("Extracted %s narratable chapters from %s", len(chapters), path)
        return metadata, chapters

    def _extract_metadata(self, doc: DocxDocument) -> BookMetadata:
        """Extract title-page metadata from the first section of the document."""

        paragraphs = self._collect_paragraphs(doc, limit=20)
        if not paragraphs:
            raise ValueError("Document does not contain readable text for metadata extraction.")

        title_position, title = self._find_title(paragraphs)
        author_position, author = self._find_author(paragraphs, title_position)
        subtitle = self._find_subtitle(paragraphs, title_position, author_position)
        original_publisher = self._find_original_publisher(paragraphs)

        return BookMetadata(
            title=title,
            subtitle=subtitle,
            author=author,
            original_publisher=original_publisher,
        )

    def _extract_chapters(self, doc: DocxDocument) -> list[Chapter]:
        """Extract narratable introduction and chapter bodies from the document."""

        paragraphs = self._collect_paragraphs(doc)
        chapters: list[Chapter] = []
        current_heading: dict[str, Any] | None = None
        current_body: list[str] = []
        narration_started = False
        saw_numbered_chapter = False
        collecting_toc = False
        self.last_toc_entries = []

        for paragraph in paragraphs:
            text = paragraph.text
            style = paragraph.style

            if not narration_started and self._is_toc_heading(text):
                collecting_toc = True
                logger.debug("Detected TOC heading at paragraph %s", paragraph.index)
                continue

            if collecting_toc:
                if self._looks_like_chapter_style(style):
                    collecting_toc = False
                elif self._looks_like_toc_entry(text, style):
                    self.last_toc_entries.append(text)
                    continue
                else:
                    collecting_toc = False
                    continue

            is_heading, parsed_heading = self._is_chapter_heading(text, style)

            if not narration_started:
                if self._is_skip_section(text):
                    logger.debug("Skipping front matter paragraph %s: %s", paragraph.index, text)
                    continue
                if not is_heading or parsed_heading is None:
                    continue

                narration_started = True
                current_heading = parsed_heading
                current_body = []
                saw_numbered_chapter = parsed_heading["type"] == "chapter"
                logger.debug(
                    "Started narratable section at paragraph %s: %s",
                    paragraph.index,
                    parsed_heading["title"],
                )
                continue

            if is_heading and parsed_heading is not None:
                if parsed_heading["type"] == "introduction":
                    current_body.append(text)
                    continue

                if current_heading is None:
                    current_heading = parsed_heading
                    current_body = []
                else:
                    chapters.append(self._build_chapter(current_heading, current_body))
                    current_heading = parsed_heading
                    current_body = []
                saw_numbered_chapter = True
                logger.debug(
                    "Detected chapter %s at paragraph %s",
                    parsed_heading["number"],
                    paragraph.index,
                )
                continue

            if self._is_back_matter_section(text):
                logger.debug("Reached back matter at paragraph %s", paragraph.index)
                break

            if self._is_skip_section(text):
                logger.debug("Skipping non-narrated paragraph %s after start: %s", paragraph.index, text)
                continue

            if current_heading is not None:
                current_body.append(text)

        if current_heading is not None:
            chapters.append(self._build_chapter(current_heading, current_body))

        if saw_numbered_chapter:
            self._validate_toc(chapters)

        return chapters

    def _is_chapter_heading(self, text: str, style: str | None) -> tuple[bool, dict[str, Any] | None]:
        """
        Determine whether a paragraph is a chapter or introduction heading.

        Returns:
            A `(bool, parsed_data)` tuple, where parsed data contains `number`, `title`, and `type`.
        """

        normalized_text = self._normalize_text(text)
        if not normalized_text:
            return False, None
        if self._is_skip_section(normalized_text):
            return False, None

        style_is_heading = self._looks_like_chapter_style(style)

        for pattern in self.intro_patterns:
            match = pattern.match(normalized_text)
            if match and (style_is_heading or normalized_text.lower() in {"introduction", "prologue", "preface"}):
                title = self._normalize_text(match.group("label"))
                return True, {"number": 0, "title": title.title(), "type": "introduction"}

        for pattern in self.chapter_patterns:
            match = pattern.match(normalized_text)
            if not match:
                continue

            if pattern is self.chapter_patterns[1] and not style_is_heading:
                continue

            chapter_number = self._coerce_chapter_number(match.group("number"))
            if chapter_number is None:
                continue

            title = self._normalize_text(match.group("title"))
            if pattern is self.chapter_patterns[1] and not title:
                continue

            return True, {
                "number": chapter_number,
                "title": title or f"Chapter {chapter_number}",
                "type": "chapter",
            }

        return False, None

    def _is_skip_section(self, text: str) -> bool:
        """Return whether a section should be skipped for narration."""

        normalized_text = self._normalize_heading_for_skip_rules(text)
        return any(pattern.match(normalized_text) for pattern in self.front_matter_patterns + self.back_matter_patterns)

    def _count_words(self, text: str) -> int:
        """Count words in the provided text."""

        return len(re.findall(r"\b[\w']+\b", text))

    def _collect_paragraphs(self, doc: DocxDocument, limit: int | None = None) -> list[_ParagraphInfo]:
        """Return normalized paragraph data for the document."""

        collected: list[_ParagraphInfo] = []
        source = doc.paragraphs if limit is None else doc.paragraphs[:limit]

        for index, paragraph in enumerate(source):
            text = self._normalize_text(paragraph.text)
            if not text:
                continue
            collected.append(
                _ParagraphInfo(
                    index=index,
                    text=text,
                    style=self._paragraph_style(paragraph),
                    paragraph=paragraph,
                )
            )

        return collected

    def _find_title(self, paragraphs: list[_ParagraphInfo]) -> tuple[int, str]:
        """Find the most likely title paragraph from the front matter."""

        for position, paragraph in enumerate(paragraphs[:6]):
            if paragraph.style and paragraph.style.lower() == "title" and not self._is_skip_section(paragraph.text):
                return position, paragraph.text

        for position, paragraph in enumerate(paragraphs[:6]):
            if self._is_skip_section(paragraph.text) or self._looks_like_credit_or_note(paragraph.text):
                continue
            if self._paragraph_is_emphasized(paragraph):
                return position, paragraph.text

        for position, paragraph in enumerate(paragraphs[:6]):
            if self._is_skip_section(paragraph.text) or self._looks_like_credit_or_note(paragraph.text):
                continue
            return position, paragraph.text

        raise ValueError("Unable to determine the book title from the opening paragraphs.")

    def _find_author(self, paragraphs: list[_ParagraphInfo], title_position: int) -> tuple[int, str]:
        """Find the author line in the front matter."""

        search_window = paragraphs[title_position + 1 : title_position + 10]
        for offset, paragraph in enumerate(search_window, start=title_position + 1):
            for pattern in self.author_patterns:
                match = pattern.match(paragraph.text)
                if match:
                    return offset, self._normalize_text(match.group("author"))

        for offset, paragraph in enumerate(search_window, start=title_position + 1):
            if self._looks_like_author_name(paragraph.text):
                return offset, paragraph.text

        raise ValueError("Unable to determine the author from the opening paragraphs.")

    def _find_subtitle(
        self,
        paragraphs: list[_ParagraphInfo],
        title_position: int,
        author_position: int,
    ) -> str | None:
        """Return subtitle text found between the title and author lines."""

        subtitle_parts: list[str] = []
        for paragraph in paragraphs[title_position + 1 : author_position]:
            if self._is_skip_section(paragraph.text):
                continue
            if self._looks_like_credit_or_note(paragraph.text):
                continue
            subtitle_parts.append(paragraph.text)

        subtitle = " ".join(subtitle_parts).strip()
        return subtitle or None

    def _find_original_publisher(self, paragraphs: list[_ParagraphInfo]) -> str | None:
        """Look for publisher metadata near the front of the document."""

        for paragraph in paragraphs[:20]:
            for pattern in self.publisher_patterns:
                match = pattern.match(paragraph.text)
                if match:
                    return self._normalize_text(match.group("publisher"))
        return None

    def _build_chapter(self, heading: dict[str, Any], body_paragraphs: list[str]) -> Chapter:
        """Build a chapter object from a heading plus collected body paragraphs."""

        raw_text = "\n\n".join(paragraph for paragraph in body_paragraphs if paragraph).strip()
        if not raw_text:
            raise ValueError(f"Detected {heading['type']} '{heading['title']}' without body text.")

        return Chapter(
            number=heading["number"],
            title=heading["title"],
            type=heading["type"],
            raw_text=raw_text,
            word_count=self._count_words(raw_text),
        )

    def _validate_toc(self, chapters: list[Chapter]) -> None:
        """Log TOC mismatches when a table of contents was detected."""

        if not self.last_toc_entries:
            return

        normalized_toc = {self._comparison_key(entry) for entry in self.last_toc_entries}
        chapter_titles = {
            self._comparison_key(chapter.title)
            for chapter in chapters
            if chapter.type == "chapter"
        }
        missing = sorted(title for title in normalized_toc if title and title not in chapter_titles)
        if missing:
            logger.debug("TOC entries without matching detected chapters: %s", missing)

    def _looks_like_author_name(self, text: str) -> bool:
        """Return whether text looks like a plain author name."""

        if self._looks_like_credit_or_note(text):
            return False
        if re.search(r"\d", text):
            return False

        words = text.split()
        if not 2 <= len(words) <= 6:
            return False

        return all(re.match(r"^[A-Z][A-Za-z'.’-]*$", word.strip(",.;:")) for word in words)

    def _looks_like_credit_or_note(self, text: str) -> bool:
        """Return whether a line is clearly metadata but not subtitle content."""

        normalized_text = self._normalize_text(text)
        return bool(
            re.match(r"^(translated|adapted|edited|illustrated)\s+by\b", normalized_text, re.IGNORECASE)
            or re.match(r"^(visit|www\.|https?://)", normalized_text, re.IGNORECASE)
            or "libraryofalexandria.com" in normalized_text.lower()
        )

    def _looks_like_toc_entry(self, text: str, style: str | None) -> bool:
        """Return whether a paragraph looks like a short TOC entry."""

        if self._looks_like_chapter_style(style):
            return False
        if self._looks_like_credit_or_note(text):
            return False
        return len(text.split()) <= 16

    def _is_toc_heading(self, text: str) -> bool:
        """Return whether text marks the start of a table of contents."""

        return bool(re.match(r"^(table of contents|contents)\b", self._normalize_text(text), re.IGNORECASE))

    def _is_back_matter_section(self, text: str) -> bool:
        """Return whether text marks terminal back matter."""

        normalized_text = self._normalize_heading_for_skip_rules(text)
        return any(pattern.match(normalized_text) for pattern in self.back_matter_patterns)

    def _looks_like_chapter_style(self, style: str | None) -> bool:
        """Return whether a paragraph style looks like a chapter heading style."""

        if not style:
            return False
        style_lower = style.lower()
        return style_lower.startswith("heading")

    def _paragraph_is_emphasized(self, paragraph: _ParagraphInfo) -> bool:
        """Return whether paragraph formatting makes it a strong title candidate."""

        if paragraph.style and "title" in paragraph.style.lower():
            return True
        return any(run.bold for run in paragraph.paragraph.runs)

    def _paragraph_style(self, paragraph: Paragraph) -> str | None:
        """Return the paragraph style name when available."""

        return paragraph.style.name if paragraph.style is not None else None

    def _normalize_text(self, text: str) -> str:
        """Collapse internal whitespace while preserving characters."""

        return " ".join(text.replace("\xa0", " ").split())

    def _normalize_heading_for_skip_rules(self, text: str) -> str:
        """Normalize heading text for punctuation-insensitive skip comparisons."""

        normalized = self._normalize_text(text).casefold()
        normalized = re.sub(r"[^\w\s]", " ", normalized)
        return re.sub(r"\s+", " ", normalized).strip()

    def _comparison_key(self, text: str) -> str:
        """Normalize heading text for loose comparisons."""

        return re.sub(r"[^a-z0-9]+", "", text.casefold())

    def _coerce_chapter_number(self, value: str) -> int | None:
        """Convert a chapter number token into an integer when possible."""

        normalized_value = value.strip().strip(".:").replace("-", " ").casefold()
        if normalized_value.isdigit():
            return int(normalized_value)
        if re.fullmatch(r"[ivxlcdm]+", normalized_value, re.IGNORECASE):
            return self._roman_to_int(normalized_value.upper())
        if normalized_value in self.word_number_map:
            return self.word_number_map[normalized_value]
        return None

    def _roman_to_int(self, value: str) -> int:
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
        previous = 0

        for character in reversed(value):
            current = numerals[character]
            if current < previous:
                total -= current
            else:
                total += current
                previous = current

        return total
