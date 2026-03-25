"""Parser factory auto-detection tests."""

from __future__ import annotations

import logging
from pathlib import Path

from _pytest.logging import LogCaptureFixture

from src.parser import DocxParser, EPUBParser, ManuscriptParserFactory, PDFParser
from tests.parser_fixture_utils import create_consistency_fixture, create_sample_epub, create_sample_pdf, default_book_sections


def test_priority_docx_over_epub(tmp_path: Path) -> None:
    """Prefer DOCX when both DOCX and EPUB are present."""

    fixture_paths = create_consistency_fixture(tmp_path)

    parser, manuscript_path = ManuscriptParserFactory.get_parser(tmp_path)

    assert isinstance(parser, DocxParser)
    assert manuscript_path == fixture_paths["docx"]


def test_priority_epub_over_pdf(tmp_path: Path) -> None:
    """Prefer EPUB when DOCX is absent and EPUB plus PDF are present."""

    epub_path = tmp_path / "book.epub"
    pdf_path = tmp_path / "book.pdf"
    create_sample_epub(
        epub_path,
        title="The Test Chronicle",
        subtitle="A Detective Story",
        author="Jane Doe",
        sections=default_book_sections(),
    )
    create_sample_pdf(
        pdf_path,
        title="The Test Chronicle",
        subtitle="A Detective Story",
        author="Jane Doe",
        sections=default_book_sections(),
    )

    parser, manuscript_path = ManuscriptParserFactory.get_parser(tmp_path)

    assert isinstance(parser, EPUBParser)
    assert manuscript_path == epub_path


def test_fallback_to_pdf(tmp_path: Path) -> None:
    """Use PDF when it is the only supported manuscript file."""

    pdf_path = tmp_path / "book.pdf"
    create_sample_pdf(
        pdf_path,
        title="The Test Chronicle",
        subtitle="A Detective Story",
        author="Jane Doe",
        sections=default_book_sections(),
    )

    parser, manuscript_path = ManuscriptParserFactory.get_parser(tmp_path)

    assert isinstance(parser, PDFParser)
    assert manuscript_path == pdf_path


def test_no_format_found(tmp_path: Path) -> None:
    """Return no parser when a folder has no supported manuscript files."""

    (tmp_path / "notes.txt").write_text("unsupported", encoding="utf-8")

    parser, manuscript_path = ManuscriptParserFactory.get_parser(tmp_path)

    assert parser is None
    assert manuscript_path is None


def test_logging(tmp_path: Path, caplog: LogCaptureFixture) -> None:
    """Log which manuscript format was selected."""

    pdf_path = tmp_path / "book.pdf"
    create_sample_pdf(
        pdf_path,
        title="The Test Chronicle",
        subtitle="A Detective Story",
        author="Jane Doe",
        sections=default_book_sections(),
    )

    with caplog.at_level(logging.INFO):
        parser, manuscript_path = ManuscriptParserFactory.get_parser(tmp_path)

    assert isinstance(parser, PDFParser)
    assert manuscript_path == pdf_path
    assert "Using PDF parser" in caplog.text
