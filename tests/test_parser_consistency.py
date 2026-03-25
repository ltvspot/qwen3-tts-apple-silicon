"""Cross-format manuscript parser consistency tests."""

from __future__ import annotations

from pathlib import Path

from src.parser import DocxParser, EPUBParser, PDFParser
from tests.parser_fixture_utils import create_consistency_fixture


def test_consistent_chapter_count(tmp_path: Path) -> None:
    """The same manuscript should yield the same narratable section count across formats."""

    fixture_paths = create_consistency_fixture(tmp_path)

    _docx_meta, docx_chapters = DocxParser().parse(fixture_paths["docx"])
    _epub_meta, epub_chapters = EPUBParser().parse(fixture_paths["epub"])
    _pdf_meta, pdf_chapters = PDFParser().parse(fixture_paths["pdf"])

    assert len(docx_chapters) == len(epub_chapters) == len(pdf_chapters) == 3


def test_consistent_metadata(tmp_path: Path) -> None:
    """Title and author should remain stable across manuscript formats."""

    fixture_paths = create_consistency_fixture(tmp_path)

    docx_meta, _docx_chapters = DocxParser().parse(fixture_paths["docx"])
    epub_meta, _epub_chapters = EPUBParser().parse(fixture_paths["epub"])
    pdf_meta, _pdf_chapters = PDFParser().parse(fixture_paths["pdf"])

    assert docx_meta.title == epub_meta.title == pdf_meta.title == "The Test Chronicle"
    assert docx_meta.author == epub_meta.author == pdf_meta.author == "Jane Doe"


def test_consistent_text_content(tmp_path: Path) -> None:
    """Word counts for matching sections should stay within a 10 percent variance."""

    fixture_paths = create_consistency_fixture(tmp_path)

    _docx_meta, docx_chapters = DocxParser().parse(fixture_paths["docx"])
    _epub_meta, epub_chapters = EPUBParser().parse(fixture_paths["epub"])
    _pdf_meta, pdf_chapters = PDFParser().parse(fixture_paths["pdf"])

    for docx_chapter, epub_chapter, pdf_chapter in zip(docx_chapters, epub_chapters, pdf_chapters, strict=True):
        assert abs(docx_chapter.word_count - epub_chapter.word_count) / docx_chapter.word_count <= 0.10
        assert abs(docx_chapter.word_count - pdf_chapter.word_count) / docx_chapter.word_count <= 0.10


def test_same_skip_rules(tmp_path: Path) -> None:
    """Front matter and back matter should stay out of narratable section text across formats."""

    fixture_paths = create_consistency_fixture(tmp_path)

    parsers = [
        DocxParser().parse(fixture_paths["docx"])[1],
        EPUBParser().parse(fixture_paths["epub"])[1],
        PDFParser().parse(fixture_paths["pdf"])[1],
    ]

    for chapters in parsers:
        assert all("Copyright 2026" not in chapter.raw_text for chapter in chapters)
        assert all("Table of Contents" not in chapter.raw_text for chapter in chapters)
        assert all("Thank You for Reading" not in chapter.raw_text for chapter in chapters)
