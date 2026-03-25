"""EPUB parser tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.parser import EPUBParser
from tests.parser_fixture_utils import SectionSpec, create_sample_epub, default_book_sections


def test_parse_epub_basic(tmp_path: Path) -> None:
    """Parse a valid EPUB and return narratable sections plus metadata."""

    epub_path = tmp_path / "book.epub"
    create_sample_epub(
        epub_path,
        title="The Test Chronicle",
        subtitle="A Detective Story",
        author="Jane Doe",
        sections=default_book_sections(),
    )

    metadata, chapters = EPUBParser().parse(epub_path)

    assert metadata.title == "The Test Chronicle"
    assert metadata.subtitle == "A Detective Story"
    assert metadata.author == "Jane Doe"
    assert [chapter.type for chapter in chapters] == ["introduction", "chapter", "chapter"]
    assert chapters[0].title == "Introduction"
    assert chapters[1].title == "The Beginning"
    assert chapters[2].title == "The Twist"
    assert all(chapter.word_count > 5 for chapter in chapters)


def test_epub_metadata_extraction(tmp_path: Path) -> None:
    """Extract title, author, and subtitle from EPUB package metadata."""

    epub_path = tmp_path / "metadata.epub"
    create_sample_epub(
        epub_path,
        title="The Clockwork Garden",
        subtitle="Specimens and Secrets",
        author="R. Finch",
        sections=default_book_sections(),
    )

    metadata, _chapters = EPUBParser().parse(epub_path)

    assert metadata.title == "The Clockwork Garden"
    assert metadata.subtitle == "Specimens and Secrets"
    assert metadata.author == "R. Finch"


def test_epub_skip_sections(tmp_path: Path) -> None:
    """Skip title-page, TOC, and back-matter sections."""

    epub_path = tmp_path / "skip.epub"
    create_sample_epub(
        epub_path,
        title="The Test Chronicle",
        subtitle="A Detective Story",
        author="Jane Doe",
        sections=default_book_sections(),
    )

    _metadata, chapters = EPUBParser().parse(epub_path)
    chapter_titles = [chapter.title for chapter in chapters]

    assert "Title Page" not in chapter_titles
    assert "Table of Contents" not in chapter_titles
    assert "Thank You for Reading" not in chapter_titles
    assert all("Skipped back matter." not in chapter.raw_text for chapter in chapters)


def test_epub_heading_detection(tmp_path: Path) -> None:
    """Recognize chapter sections from any heading tag level."""

    epub_path = tmp_path / "headings.epub"
    sections = [
        SectionSpec("Chapter I. One", ["First body."], heading_tag="h1"),
        SectionSpec("Chapter II. Two", ["Second body."], heading_tag="h2"),
        SectionSpec("Chapter III. Three", ["Third body."], heading_tag="h3"),
        SectionSpec("Chapter IV. Four", ["Fourth body."], heading_tag="h4"),
        SectionSpec("Chapter V. Five", ["Fifth body."], heading_tag="h5"),
        SectionSpec("Chapter VI. Six", ["Sixth body."], heading_tag="h6"),
    ]
    create_sample_epub(
        epub_path,
        title="Heading Levels",
        subtitle=None,
        author="Jane Doe",
        sections=sections,
    )

    _metadata, chapters = EPUBParser().parse(epub_path)

    assert len(chapters) == 6
    assert [chapter.number for chapter in chapters] == [1, 2, 3, 4, 5, 6]
    assert chapters[-1].title == "Six"


def test_epub_corrupted_file(tmp_path: Path) -> None:
    """Raise a ValueError for invalid EPUB payloads."""

    epub_path = tmp_path / "broken.epub"
    epub_path.write_text("not an epub", encoding="utf-8")

    with pytest.raises(ValueError, match="Failed to read EPUB file"):
        EPUBParser().parse(epub_path)


def test_epub_empty_chapters(tmp_path: Path) -> None:
    """Skip heading sections that do not contain narratable body text."""

    epub_path = tmp_path / "empty.epub"
    sections = [
        SectionSpec("Chapter I. Empty", []),
        SectionSpec("Chapter II. Real", ["This chapter has actual content."]),
    ]
    create_sample_epub(
        epub_path,
        title="Sparse Book",
        subtitle=None,
        author="Jane Doe",
        sections=sections,
    )

    _metadata, chapters = EPUBParser().parse(epub_path)

    assert len(chapters) == 1
    assert chapters[0].number == 2
    assert chapters[0].title == "Real"
