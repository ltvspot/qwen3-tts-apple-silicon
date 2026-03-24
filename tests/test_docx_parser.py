"""DOCX parser and text cleaning tests."""

from __future__ import annotations

from pathlib import Path

from docx import Document

from src.parser import CreditsGenerator, DocxParser, TextCleaner


def _find_sherlock_docx() -> Path | None:
    """Return the preferred Sherlock Holmes DOCX test manuscript."""

    manuscripts_path = Path(__file__).resolve().parent.parent / "Formatted Manuscripts"
    candidates = sorted(manuscripts_path.glob("0906*/*Clean.docx"))
    if candidates:
        return candidates[0]

    fallback_candidates = sorted(manuscripts_path.glob("0906*/*.docx"))
    return fallback_candidates[0] if fallback_candidates else None


def test_parse_sherlock_holmes_manuscript() -> None:
    """Parse the real Sherlock Holmes manuscript and validate core structure."""

    parser = DocxParser()
    test_docx = _find_sherlock_docx()

    assert test_docx is not None, "Sherlock Holmes manuscript not found."

    metadata, chapters = parser.parse(test_docx)

    assert metadata.title == "The Adventures of Sherlock Holmes"
    assert metadata.author == "Arthur Conan Doyle"
    assert metadata.subtitle is not None
    assert "Mystery" in metadata.subtitle

    assert len(chapters) == 13
    assert chapters[0].number == 0
    assert chapters[0].type == "introduction"
    assert chapters[0].title == "Introduction"
    assert chapters[0].word_count > 100
    assert "\n\n" in chapters[0].raw_text

    numbered_chapters = [chapter for chapter in chapters if chapter.type == "chapter"]
    assert len(numbered_chapters) == 12
    assert numbered_chapters[0].number == 1
    assert numbered_chapters[0].title == "A Scandal in Bohemia"
    assert numbered_chapters[-1].number == 12
    assert numbered_chapters[-1].title == "The Adventure of The Copper Beeches"
    assert all("Preface Message to the Reader" not in chapter.raw_text for chapter in chapters)
    assert all("Thank You for Reading" not in chapter.raw_text for chapter in chapters)


def test_parse_synthetic_docx_skip_rules_and_intro(tmp_path: Path) -> None:
    """Keep TOC entries and in-chapter subheads from becoming false chapter breaks."""

    document = Document()
    document.add_paragraph("A Sample Mystery", style="Title")
    document.add_paragraph("by Jane Doe")
    document.add_paragraph("Copyright 2026")
    document.add_paragraph("Table of Contents")
    document.add_paragraph("Chapter I. The Beginning")
    document.add_paragraph("Chapter II. The Middle")
    document.add_paragraph("Introduction", style="Heading 1")
    document.add_paragraph("Opening setup.")
    document.add_paragraph("Chapter I. The Beginning", style="Heading 1")
    document.add_paragraph("The story starts here.")
    document.add_paragraph("I.", style="Heading 2")
    document.add_paragraph("A subsection should remain inside chapter one.")
    document.add_paragraph("Thank You for Reading")

    docx_path = tmp_path / "sample.docx"
    document.save(docx_path)

    metadata, chapters = DocxParser().parse(docx_path)

    assert metadata.title == "A Sample Mystery"
    assert metadata.author == "Jane Doe"
    assert [chapter.type for chapter in chapters] == ["introduction", "chapter"]
    assert chapters[0].number == 0
    assert chapters[1].number == 1
    assert chapters[1].title == "The Beginning"
    assert "A subsection should remain inside chapter one." in chapters[1].raw_text
    assert "Thank You for Reading" not in chapters[1].raw_text


def test_text_cleaning() -> None:
    """Normalize page markers, abbreviations, and punctuation for TTS."""

    cleaner = TextCleaner()
    dirty_text = (
        "Page 42\n"
        "Dr. Watson and Mr. Holmes discussed the case—a difficult one…\n"
        "They met on Baker St. near St. Paul's.\n"
        "Page 43"
    )

    cleaned_text = cleaner.clean(dirty_text)

    assert "Page 42" not in cleaned_text
    assert "Page 43" not in cleaned_text
    assert "Doctor Watson" in cleaned_text
    assert "Mister Holmes" in cleaned_text
    assert "Baker Street" in cleaned_text
    assert "Saint Paul's" in cleaned_text
    assert "..." in cleaned_text
    assert "—" in cleaned_text


def test_credits_generation() -> None:
    """Generate opening and closing credits with the required metadata."""

    opening = CreditsGenerator.generate_opening_credits(
        title="The Sherlock Holmes Mysteries",
        subtitle="A Complete Collection",
        author="Arthur Conan Doyle",
    )
    closing = CreditsGenerator.generate_closing_credits(
        title="The Sherlock Holmes Mysteries",
        subtitle="A Complete Collection",
        author="Arthur Conan Doyle",
    )

    assert "This is The Sherlock Holmes Mysteries." in opening
    assert "A Complete Collection." in opening
    assert "Written by Arthur Conan Doyle." in opening
    assert "Narrated by Kent Zimering." in opening

    assert "This was The Sherlock Holmes Mysteries." in closing
    assert "A Complete Collection." in closing
    assert "Written by Arthur Conan Doyle." in closing
    assert "Narrated by Kent Zimering." in closing
