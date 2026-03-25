"""PDF parser tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.parser import PDFParser
from tests.parser_fixture_utils import SectionSpec, create_sample_pdf, default_book_sections, write_text_pdf


def test_parse_pdf_basic(tmp_path: Path) -> None:
    """Parse a valid text PDF and return narratable sections plus metadata."""

    pdf_path = tmp_path / "book.pdf"
    create_sample_pdf(
        pdf_path,
        title="The Test Chronicle",
        subtitle="A Detective Story",
        author="Jane Doe",
        sections=default_book_sections(),
    )

    metadata, chapters = PDFParser().parse(pdf_path)

    assert metadata.title == "The Test Chronicle"
    assert metadata.subtitle == "A Detective Story"
    assert metadata.author == "Jane Doe"
    assert [chapter.type for chapter in chapters] == ["introduction", "chapter", "chapter"]
    assert chapters[0].title == "Introduction"
    assert chapters[1].title == "The Beginning"
    assert chapters[2].title == "The Twist"


def test_pdf_chapter_pattern_detection(tmp_path: Path) -> None:
    """Detect numbered chapter headings across the supported plain-text patterns."""

    pdf_path = tmp_path / "patterns.pdf"
    pages = [
        ["Pattern Book", "by Jane Doe"],
        ["Introduction", "Opening setup."],
        ["Chapter I. One", "First body."],
        ["2. Two", "Second body."],
        ["Part III. Three", "Third body."],
        ["Thank You for Reading"],
    ]
    write_text_pdf(pdf_path, pages, metadata={"Title": "Pattern Book", "Author": "Jane Doe"})

    _metadata, chapters = PDFParser().parse(pdf_path)

    assert len(chapters) == 4
    assert [chapter.number for chapter in chapters] == [0, 1, 2, 3]
    assert [chapter.title for chapter in chapters] == ["Introduction", "One", "Two", "Three"]


def test_pdf_skip_sections(tmp_path: Path) -> None:
    """Skip non-narrated front matter and terminal back matter headings."""

    pdf_path = tmp_path / "skip.pdf"
    pages = [
        ["The Test Chronicle", "by Jane Doe"],
        ["Foreword", "Skipped foreword content."],
        ["Copyright 2026", "Table of Contents", "Chapter I. The Beginning"],
        ["Chapter I. The Beginning", "The story starts here."],
        ["Afterword", "Skipped ending content."],
    ]
    write_text_pdf(pdf_path, pages, metadata={"Title": "The Test Chronicle", "Author": "Jane Doe"})

    _metadata, chapters = PDFParser().parse(pdf_path)

    assert len(chapters) == 1
    assert chapters[0].title == "The Beginning"
    assert "Skipped foreword content." not in chapters[0].raw_text
    assert "Skipped ending content." not in chapters[0].raw_text


def test_pdf_missing_metadata(tmp_path: Path) -> None:
    """Fallback to first-page text when PDF metadata is absent."""

    pdf_path = tmp_path / "nometa.pdf"
    create_sample_pdf(
        pdf_path,
        title="The Test Chronicle",
        subtitle="A Detective Story",
        author="Jane Doe",
        sections=default_book_sections(),
        include_metadata=False,
    )

    metadata, _chapters = PDFParser().parse(pdf_path)

    assert metadata.title == "The Test Chronicle"
    assert metadata.author == "Jane Doe"


def test_pdf_corrupted_file(tmp_path: Path) -> None:
    """Raise a ValueError for invalid PDF payloads."""

    pdf_path = tmp_path / "broken.pdf"
    pdf_path.write_text("not a pdf", encoding="utf-8")

    with pytest.raises(ValueError, match="Failed to read PDF file"):
        PDFParser().parse(pdf_path)


def test_pdf_large_document(tmp_path: Path) -> None:
    """Handle a large multi-page PDF without losing the final chapter body."""

    pdf_path = tmp_path / "large.pdf"
    create_sample_pdf(
        pdf_path,
        title="Large Book",
        subtitle=None,
        author="Jane Doe",
        sections=[
            SectionSpec("Chapter I. Long Case", ["The investigation begins."]),
        ],
        extra_pages=505,
    )

    _metadata, chapters = PDFParser().parse(pdf_path)

    assert len(chapters) == 1
    assert chapters[0].title == "Long Case"
    assert "Continuation page 505 for large document coverage." in chapters[0].raw_text
