"""DOCX parser and text cleaning tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from docx import Document
from docx.enum.style import WD_STYLE_TYPE

from src.parser import CreditsGenerator, DocxParser, ManuscriptParserFactory, TextCleaner
from src.parser.common import should_skip_heading


def _find_sherlock_docx() -> Path | None:
    """Return the preferred Sherlock Holmes DOCX test manuscript."""

    manuscripts_path = Path(__file__).resolve().parent.parent / "Formatted Manuscripts"
    candidates = sorted(manuscripts_path.glob("0906*/*Clean.docx"))
    if candidates:
        return candidates[0]

    fallback_candidates = sorted(manuscripts_path.glob("0906*/*.docx"))
    return fallback_candidates[0] if fallback_candidates else None


def _write_docx(docx_path: Path, paragraphs: list[tuple[str, str | None]]) -> None:
    """Create a DOCX file from `(text, style)` paragraph pairs."""

    document = Document()
    available_styles = {style.name for style in document.styles}

    for _text, style in paragraphs:
        if style is not None and style not in available_styles:
            document.styles.add_style(style, WD_STYLE_TYPE.PARAGRAPH)
            available_styles.add(style)

    for text, style in paragraphs:
        if style is None:
            document.add_paragraph(text)
        else:
            document.add_paragraph(text, style=style)
    document.save(docx_path)


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


def test_build_chapter_returns_none_for_empty_body() -> None:
    """Empty section bodies should be skipped instead of raising exceptions."""

    parser = DocxParser()

    assert parser._build_chapter({"number": 1, "title": "Chapter 1", "type": "chapter"}, ["", "   "]) is None


def test_parse_skips_empty_chapter_sections_instead_of_crashing(tmp_path: Path) -> None:
    """A heading without body text should be omitted while later chapters still parse."""

    docx_path = tmp_path / "empty-chapter.docx"
    _write_docx(
        docx_path,
        [
            ("A Stoic Handbook", "Title"),
            ("by Jane Doe", None),
            ("Chapter I. Empty Start", "Heading 1"),
            ("Chapter II. Real Chapter", "Heading 1"),
            ("This chapter has actual body text.", None),
        ],
    )

    metadata, chapters = DocxParser().parse(docx_path)

    assert metadata.author == "Jane Doe"
    assert [chapter.number for chapter in chapters] == [2]
    assert chapters[0].title == "Real Chapter"
    assert chapters[0].raw_text == "This chapter has actual body text."


def test_parse_skips_long_toc_entries_and_finds_real_chapters(tmp_path: Path) -> None:
    """Long TOC entries with TOC styles should not become empty chapters."""

    long_chapter_one = (
        "Chapter I: In Which the North Polar Practical Association Decides That It Is "
        "Absolutely Necessary to Reach the Pole"
    )
    long_chapter_two = (
        "Chapter II: In Which the Delegation Is Sent Forth To Investigate the Frozen "
        "Regions and Report Back Quickly"
    )
    docx_path = tmp_path / "long-toc.docx"
    _write_docx(
        docx_path,
        [
            ("An Arctic Adventure", "Title"),
            ("by Jane Doe", None),
            ("Table of Contents", None),
            (long_chapter_one, "TOC 1"),
            (long_chapter_two, "TOC 1"),
            (long_chapter_one, "Heading 1"),
            ("The expedition begins in earnest.", None),
            (long_chapter_two, "Heading 1"),
            ("The report confirms the danger ahead.", None),
        ],
    )

    parser = DocxParser()
    _metadata, chapters = parser.parse(docx_path)

    assert [chapter.number for chapter in chapters] == [1, 2]
    assert chapters[0].title == (
        "In Which the North Polar Practical Association Decides That It Is Absolutely Necessary to Reach the Pole"
    )
    assert chapters[1].title == (
        "In Which the Delegation Is Sent Forth To Investigate the Frozen Regions and Report Back Quickly"
    )
    assert parser.last_toc_entries == [long_chapter_one, long_chapter_two]


def test_parse_keeps_collecting_toc_for_toc_styled_note_paragraphs(tmp_path: Path) -> None:
    """TOC-styled note-like lines should not force an early exit from TOC mode."""

    docx_path = tmp_path / "toc-note.docx"
    _write_docx(
        docx_path,
        [
            ("Collected Speeches", "Title"),
            ("by Jane Doe", None),
            ("Table of Contents", None),
            ("Volume I", "TOC 1"),
            ("Chapter I. Opening Address", "TOC 1"),
            ("Chapter I. Opening Address", "Heading 1"),
            ("Actual chapter body text.", None),
        ],
    )

    parser = DocxParser()
    _metadata, chapters = parser.parse(docx_path)

    assert [chapter.number for chapter in chapters] == [1]
    assert parser.last_toc_entries == ["Volume I", "Chapter I. Opening Address"]


def test_toc_entries_with_page_numbers_are_detected() -> None:
    """TOC entries with trailing page numbers should be recognized after normalization."""

    parser = DocxParser()

    assert parser._looks_like_toc_entry("Chapter II. The Middle\t42", None) is True
    assert parser._looks_like_toc_entry("Chapter II. The Middle 42", None) is True


def test_modern_translation_line_is_not_treated_as_author(tmp_path: Path) -> None:
    """Edition descriptors should not beat a real author-like line."""

    docx_path = tmp_path / "author-false-positive.docx"
    _write_docx(
        docx_path,
        [
            ("Ancient Wisdom", "Title"),
            ("A Modern Translation", None),
            ("Jane Doe", None),
            ("Chapter I. Opening", "Heading 1"),
            ("Body text.", None),
        ],
    )

    metadata, _chapters = DocxParser().parse(docx_path)

    assert metadata.author == "Jane Doe"
    assert metadata.subtitle is None


def test_parse_falls_back_to_unknown_author_without_crashing(tmp_path: Path) -> None:
    """Missing author metadata should return a fallback instead of raising."""

    docx_path = tmp_path / "unknown-author.docx"
    _write_docx(
        docx_path,
        [
            ("Meditations on Resilience", "Title"),
            ("Chapter I. Opening", "Heading 1"),
            ("This manuscript omits the author line.", None),
        ],
    )

    metadata, chapters = DocxParser().parse(docx_path)

    assert metadata.author == "Unknown Author"
    assert len(chapters) == 1
    assert chapters[0].title == "Opening"


def test_fallback_single_chapter(tmp_path: Path) -> None:
    """Books without headings should fall back to one Full Text chapter."""

    body_opening = (
        "This manuscript begins immediately with sustained body prose that exceeds thirty words, "
        "so the parser should treat it as the start of narratable content instead of discarding "
        "the whole document for lacking explicit chapter headings."
    )
    body_followup = (
        "A second paragraph should stay attached to the same fallback chapter, preserving spacing "
        "and ensuring the parser emits one continuous narratable section for the full document."
    )
    docx_path = tmp_path / "full-text-fallback.docx"
    _write_docx(
        docx_path,
        [
            ("Dialogues on Strategy", "Title"),
            ("by Jane Doe", None),
            ("Copyright 2026", None),
            (body_opening, None),
            (body_followup, None),
            ("Thank You for Reading", None),
        ],
    )

    metadata, chapters = DocxParser().parse(docx_path)

    assert metadata.author == "Jane Doe"
    assert len(chapters) == 1
    assert chapters[0].number == 1
    assert chapters[0].title == "Full Text"
    assert chapters[0].type == "chapter"
    assert chapters[0].raw_text == f"{body_opening}\n\n{body_followup}"


def test_fallback_empty_document(tmp_path: Path) -> None:
    """Front-matter-only documents should still fail with diagnostics."""

    docx_path = tmp_path / "front-matter-only.docx"
    _write_docx(
        docx_path,
        [
            ("A Book Without Chapters", "Title"),
            ("by Jane Doe", None),
            ("Copyright 2026", None),
            ("Table of Contents", None),
            ("Thank You for Reading", None),
        ],
    )

    with pytest.raises(ValueError, match=r"No narratable chapters detected in front-matter-only\.docx"):
        DocxParser().parse(docx_path)


def test_extract_author_from_folder_name() -> None:
    """Known-author folder patterns should resolve to canonical author names."""

    parser = DocxParser()

    assert parser._extract_author_from_folder("001-The-Art-of-War-Sun-Tzu-6x9-142") == "Sun Tzu"
    assert (
        parser._extract_author_from_folder("010-Beyond-Good-and-Evil-Friedrich-Nietzsche-6x9-262")
        == "Friedrich Nietzsche"
    )
    assert parser._extract_author_from_folder("020-The-Kybalion-6x9-113") is None


def test_factory_uses_folder_hint_for_unknown_docx_authors(tmp_path: Path) -> None:
    """Factory parsing should upgrade Unknown Author when the folder embeds a known author."""

    manuscript_folder = tmp_path / "001-The-Art-of-War-Sun-Tzu-6x9-142"
    manuscript_folder.mkdir()
    docx_path = manuscript_folder / "manuscript.docx"
    _write_docx(
        docx_path,
        [
            ("The Art of War", "Title"),
            ("Chapter I. Laying Plans", "Heading 1"),
            ("Appear weak when you are strong.", None),
        ],
    )

    metadata, chapters, manuscript_path = ManuscriptParserFactory.parse_manuscript(manuscript_folder)

    assert metadata.author == "Sun Tzu"
    assert len(chapters) == 1
    assert manuscript_path == docx_path


def test_parse_with_folder_hint_uses_title_lookup_for_unknown_author(tmp_path: Path) -> None:
    """Known titles should resolve Unknown Author when the folder name has no author hint."""

    docx_path = tmp_path / "known-title.docx"
    _write_docx(
        docx_path,
        [
            ("Metaphysics", "Title"),
            ("Chapter I. First Principles", "Heading 1"),
            ("All men by nature desire to know.", None),
        ],
    )

    metadata, chapters = DocxParser().parse_with_folder_hint(docx_path, folder_name="001-Metaphysics-6x9-250")

    assert metadata.author == "Aristotle"
    assert len(chapters) == 1


def test_known_titles_expansion() -> None:
    """Expanded title mappings should cover the new canonical works."""

    parser = DocxParser()

    assert parser.KNOWN_TITLES["jane eyre"] == "Charlotte Brontë"
    assert parser.KNOWN_TITLES["don quixote"] == "Miguel de Cervantes"
    assert parser.KNOWN_TITLES["the communist manifesto"] == "Karl Marx"
    assert parser.KNOWN_TITLES["the epic of gilgamesh"] == "Anonymous"


def test_known_authors_expansion() -> None:
    """Expanded folder-author mappings should resolve the new canonical authors."""

    parser = DocxParser()

    assert parser.KNOWN_AUTHORS["xenophon"] == "Xenophon"
    assert parser.KNOWN_AUTHORS["charlotte brontë"] == "Charlotte Brontë"


def test_title_lookup_with_ampersand(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Title lookup should normalize ampersands to 'and' before failing."""

    monkeypatch.setattr(
        DocxParser,
        "KNOWN_TITLES",
        {"the complete strategy and war collection": "Various Authors"},
    )

    docx_path = tmp_path / "ampersand-title.docx"
    _write_docx(
        docx_path,
        [
            ("The Complete Strategy & War Collection", "Title"),
            ("Chapter I. Opening", "Heading 1"),
            ("Collected strategic writings begin here.", None),
        ],
    )

    metadata, _chapters = DocxParser().parse_with_folder_hint(
        docx_path,
        folder_name="001-Complete-Strategy-Collection-6x9-250",
    )

    assert metadata.author == "Various Authors"


def test_folder_author_hint_takes_priority_over_title_lookup(tmp_path: Path) -> None:
    """Folder-name author extraction should win over title-based fallback."""

    docx_path = tmp_path / "known-title-priority.docx"
    _write_docx(
        docx_path,
        [
            ("Metaphysics", "Title"),
            ("Chapter I. First Principles", "Heading 1"),
            ("All men by nature desire to know.", None),
        ],
    )

    metadata, _chapters = DocxParser().parse_with_folder_hint(
        docx_path,
        folder_name="001-Metaphysics-Sun-Tzu-6x9-250",
    )

    assert metadata.author == "Sun Tzu"


def test_non_author_phrases_are_rejected_as_author_names() -> None:
    """Known subtitle and edition phrases should never pass the author heuristic."""

    parser = DocxParser()

    for phrase in parser.NON_AUTHOR_PHRASES:
        assert parser._looks_like_author_name(phrase.title()) is False


def test_no_narratable_chapters_error_includes_diagnostics(tmp_path: Path) -> None:
    """The parser should explain why no chapters were found."""

    docx_path = tmp_path / "no-chapters.docx"
    _write_docx(
        docx_path,
        [
            ("A Book Without Headings", "Title"),
            ("by Jane Doe", None),
            ("This file has body text.", None),
            ("But no narratable chapter markers.", None),
        ],
    )

    with pytest.raises(ValueError, match=r"No narratable chapters detected in no-chapters\.docx") as exc_info:
        DocxParser().parse(docx_path)

    message = str(exc_info.value)
    assert "The document has 4 paragraphs (4 non-empty)." in message
    assert "The parser requires chapter headings with 'Chapter N' format or Heading-styled paragraphs." in message


def test_skip_rules_ignore_case_and_punctuation_variants() -> None:
    """Skip rules should match front and back matter despite punctuation changes."""

    assert should_skip_heading("PREFACE - Message to the Reader")
    assert should_skip_heading("Thank You For Reading!!!")


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
