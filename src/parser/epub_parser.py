"""EPUB manuscript parsing for audiobook chapter extraction."""

from __future__ import annotations

import logging
from html.parser import HTMLParser
from pathlib import Path

import ebooklib
from ebooklib import epub

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


class EPUBParser:
    """Parse EPUB manuscripts into metadata and narratable chapters."""

    def parse(self, epub_path: str | Path) -> tuple[BookMetadata, list[Chapter]]:
        """
        Parse an EPUB file and return extracted metadata plus chapter content.

        Args:
            epub_path: Path to the EPUB manuscript.

        Returns:
            A tuple of book metadata and detected chapters.

        Raises:
            ValueError: If the file is missing, unreadable, or missing narratable content.
        """

        path = Path(epub_path)
        if not path.exists():
            raise ValueError(f"EPUB file does not exist: {path}")

        logger.info("Parsing EPUB manuscript: %s", path)

        try:
            book = epub.read_epub(str(path))
        except Exception as exc:
            raise ValueError(f"Failed to read EPUB file: {path}") from exc

        metadata = self._extract_metadata(book)
        chapters = self._extract_chapters(book)
        if not chapters:
            raise ValueError(f"No narratable chapters detected in {path}")

        logger.info("Extracted %s narratable chapters from %s", len(chapters), path)
        return metadata, chapters

    def _extract_metadata(self, book: epub.EpubBook) -> BookMetadata:
        """Extract package metadata from the EPUB manifest."""

        title = self._first_metadata_value(book, "title") or "Unknown"
        author = self._first_metadata_value(book, "creator") or "Unknown"
        subtitle = self._first_metadata_value(book, "description")
        return BookMetadata(
            title=normalize_text(title),
            subtitle=normalize_text(subtitle) if subtitle else None,
            author=normalize_text(author),
            original_publisher=None,
        )

    def _extract_chapters(self, book: epub.EpubBook) -> list[Chapter]:
        """Walk EPUB spine order and convert heading sections into chapters."""

        chapters: list[Chapter] = []
        next_generated_number = 1
        narration_started = False

        for document in self._iter_spine_documents(book):
            parser = EPUBHTMLSectionParser()
            parser.feed(document.get_body_content().decode("utf-8", errors="ignore"))
            parser.close()

            for section in parser.sections:
                heading = normalize_text(section["heading"])
                text = normalize_multiline_text(section["blocks"])
                if heading and is_front_matter_heading(heading):
                    continue
                if heading and is_back_matter_heading(heading):
                    return chapters

                parsed_heading = self._resolve_heading(heading, next_generated_number)
                if not text:
                    continue

                if parsed_heading is None:
                    if chapters:
                        previous_chapter = chapters[-1]
                        previous_chapter.raw_text = f"{previous_chapter.raw_text}\n\n{text}".strip()
                        previous_chapter.word_count = count_words(previous_chapter.raw_text)
                    continue

                if parsed_heading.type == "chapter":
                    next_generated_number = max(next_generated_number, parsed_heading.number + 1)

                chapters.append(
                    Chapter(
                        number=parsed_heading.number,
                        title=parsed_heading.title,
                        type=parsed_heading.type,
                        raw_text=text,
                        word_count=count_words(text),
                    )
                )
                narration_started = True

        return chapters

    def _iter_spine_documents(self, book: epub.EpubBook) -> list[epub.EpubHtml]:
        """Return readable spine documents in the package reading order."""

        documents: list[epub.EpubHtml] = []
        for item_id, _linear in book.spine:
            if item_id == "nav":
                continue
            item = book.get_item_with_id(item_id)
            if item is None or item.get_type() != ebooklib.ITEM_DOCUMENT:
                continue
            if not isinstance(item, epub.EpubHtml):
                continue
            if item.file_name.lower().endswith("nav.xhtml"):
                continue
            documents.append(item)
        return documents

    def _resolve_heading(self, heading: str, generated_number: int) -> ParsedHeading | None:
        """Map an EPUB heading to a narratable section definition."""

        if not heading:
            return None

        parsed_heading = classify_heading(heading)
        if parsed_heading is not None:
            return parsed_heading

        return ParsedHeading(number=generated_number, title=heading, type="chapter")

    def _first_metadata_value(self, book: epub.EpubBook, key: str) -> str | None:
        """Return the first Dublin Core metadata value for a key."""

        values = book.get_metadata("DC", key)
        if not values:
            return None
        first = values[0]
        return first[0] if isinstance(first, tuple) else str(first)


class EPUBHTMLSectionParser(HTMLParser):
    """Extract heading-delimited text blocks from EPUB XHTML documents."""

    HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
    BLOCK_TAGS = {"p", "div", "li", "blockquote"}

    def __init__(self) -> None:
        """Initialize parser state for a single XHTML document."""

        super().__init__(convert_charrefs=True)
        self.sections: list[dict[str, list[str] | str]] = []
        self.current_heading_parts: list[str] = []
        self.current_block_parts: list[str] = []
        self.current_blocks: list[str] = []
        self.in_heading = False
        self.in_block = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Track block and heading boundaries while parsing XHTML."""

        if tag in self.HEADING_TAGS:
            self._close_block()
            if self.current_heading_parts or self.current_blocks:
                self._save_section()
            self.in_heading = True
            self.current_heading_parts = []
            return

        if tag in self.BLOCK_TAGS:
            self._close_block()
            self.in_block = True
            return

        if tag == "br":
            self._close_block()

    def handle_endtag(self, tag: str) -> None:
        """Close block scopes and heading scopes when tags end."""

        if tag in self.HEADING_TAGS:
            self.in_heading = False
            return

        if tag in self.BLOCK_TAGS:
            self._close_block()
            self.in_block = False

    def handle_data(self, data: str) -> None:
        """Collect heading text and paragraph text."""

        if not data or not data.strip():
            return
        if self.in_heading:
            self.current_heading_parts.append(data)
            return
        self.current_block_parts.append(data)

    def close(self) -> None:
        """Flush the final pending section after parsing completes."""

        self._close_block()
        if self.current_heading_parts or self.current_blocks:
            self._save_section()
        super().close()

    def _close_block(self) -> None:
        """Persist the current text block when it contains readable text."""

        text = normalize_text(" ".join(self.current_block_parts))
        if text:
            self.current_blocks.append(text)
        self.current_block_parts = []

    def _save_section(self) -> None:
        """Save the current heading plus blocks as a parsed section."""

        heading = normalize_text(" ".join(self.current_heading_parts))
        self.sections.append(
            {
                "heading": heading,
                "blocks": list(self.current_blocks),
            }
        )
        self.current_heading_parts = []
        self.current_blocks = []
