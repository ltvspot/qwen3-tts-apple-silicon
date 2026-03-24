# PROMPT-02: DOCX Manuscript Parser & Text Cleaning Pipeline

**Objective:** Create a robust DOCX parser that extracts chapter structure from manuscripts, generates opening/closing credits, and provides a text cleaning pipeline for TTS preprocessing.

**Project Context:** See `/sessions/focused-happy-pasteur/mnt/Coding Folder/Qwen3-TTS - Test/CLAUDE.md` for conventions and architecture.

---

## Scope & Requirements

### 1. DOCX Parser

**File:** `src/parser/docx_parser.py`

Create a parser that reads DOCX files and extracts structured chapter data.

```python
from typing import List, Dict, Optional
from dataclasses import dataclass
from pathlib import Path
from docx import Document
import re
import logging

logger = logging.getLogger(__name__)

@dataclass
class BookMetadata:
    """Extracted title page metadata."""
    title: str
    subtitle: Optional[str]
    author: str
    original_publisher: Optional[str]

@dataclass
class Chapter:
    """Represents a single chapter."""
    number: int  # 0=intro, 1-N=chapters
    title: str
    type: str  # "introduction", "chapter", etc.
    raw_text: str
    word_count: int

class DocxParser:
    """Parse DOCX manuscripts into structured chapter data."""

    def __init__(self):
        """Initialize parser with chapter detection patterns."""
        self.chapter_patterns = [
            r'^Chapter\s+([IVX]+|[0-9]+)\s*[:.\-]?\s*(.*)$',  # Chapter I, Chapter 1
            r'^CHAPTER\s+([IVX]+|[0-9]+)\s*[:.\-]?\s*(.*)$',   # CHAPTER I
            r'^Chapter\s+([A-Z][a-z]+).*$',                     # Chapter One, Chapter Two
            r'^([IVX]+|[0-9]+)\s*[:.\-]\s*(.*)$',               # I. Title, 1. Title
        ]
        self.intro_patterns = [
            r'^(Introduction|Preface|Prologue)',
            r'^(INTRODUCTION|PREFACE|PROLOGUE)',
        ]
        self.skip_patterns = [
            r'^(Copyright|©)',
            r'^(Table of Contents|Contents)',
            r'^(Preface\s*—|Message to the Reader)',
            r'^(Thank[s]? You for Reading|Epilogue)',
        ]

    def parse(self, docx_path: str) -> tuple[BookMetadata, List[Chapter]]:
        """
        Parse a DOCX file and extract metadata and chapters.

        Args:
            docx_path: Path to the DOCX file

        Returns:
            tuple of (BookMetadata, list of Chapter objects)

        Raises:
            ValueError: If document is invalid or metadata missing
        """
        doc = Document(docx_path)
        logger.info(f"Parsing DOCX: {docx_path}")

        # Extract metadata from title page (first 10 paragraphs)
        metadata = self._extract_metadata(doc)

        # Extract chapters from body
        chapters = self._extract_chapters(doc)

        logger.info(f"Extracted {len(chapters)} chapters from {docx_path}")
        return metadata, chapters

    def _extract_metadata(self, doc: Document) -> BookMetadata:
        """
        Extract title, subtitle, author from title page.
        Looks at first 10 paragraphs for title, subtitle, author patterns.
        """
        ...

    def _extract_chapters(self, doc: Document) -> List[Chapter]:
        """
        Extract chapters by analyzing paragraph styles and content.
        Skip copyright, TOC, prefaces, thank you pages.
        """
        ...

    def _is_chapter_heading(self, text: str, style: Optional[str]) -> tuple[bool, Optional[Dict]]:
        """
        Check if text is a chapter heading.
        Returns (is_heading, parsed_data) where parsed_data has 'number' and 'title'.
        """
        ...

    def _is_skip_section(self, text: str) -> bool:
        """Check if section should be skipped (copyright, TOC, etc)."""
        ...

    def _count_words(self, text: str) -> int:
        """Count words in text."""
        return len(text.split())
```

**Key Behaviors:**

1. **Chapter Detection:**
   - Detect chapter headings via style (Heading 1, Heading 2) AND regex patterns
   - Support formats: "Chapter I.", "Chapter 1:", "CHAPTER ONE", "I. Title", "1. Title"
   - Use Roman numeral support for conversion (I→1, II→2, etc.)
   - Extract chapter title if present

2. **Metadata Extraction:**
   - Title: usually largest/boldest text in first 3 paragraphs
   - Subtitle: secondary title, often italicized
   - Author: look for "by [Name]" pattern or styled paragraph

3. **Skip Rules (Critical!):**
   - Copyright page: contains "©" or "Copyright"
   - Table of Contents: contains "Table of Contents" or "Contents"
   - Preface/Message to Reader: contains "Preface — Message to the Reader"
   - Back matter: "Thank You for Reading" or "Thank for Reading"
   - **Use for validation:** Extract TOC but don't narrate; verify chapter list matches

4. **Introduction Handling:**
   - If document has "Introduction" or "Prologue" before first numbered chapter, mark as `type="introduction"`
   - Assign number 0

5. **Text Content:**
   - Extract raw text from paragraphs between chapter headings
   - Preserve paragraph breaks as double newlines
   - Raw text will be cleaned separately (see Text Cleaner below)

---

### 2. Text Cleaning Pipeline

**File:** `src/parser/text_cleaner.py`

Create a text cleaning pipeline for TTS preprocessing.

```python
import re
from typing import Dict
import logging

logger = logging.getLogger(__name__)

class TextCleaner:
    """Clean manuscript text for TTS processing."""

    def __init__(self):
        """Initialize abbreviation mappings."""
        self.abbreviation_map = {
            r'\bDr\.\s+': 'Doctor ',
            r'\bMr\.\s+': 'Mister ',
            r'\bMrs\.\s+': 'Missus ',
            r'\bMs\.\s+': 'Ms ',
            r'\bSt\.\s+': 'Saint ',
            r'\bProf\.\s+': 'Professor ',
            r'\bRev\.\s+': 'Reverend ',
            r'\bu\.s\.\s+': 'U.S. ',
            r'\bu\.k\.\s+': 'U.K. ',
        }
        self.em_dash_variants = ['—', '–', '─', '−']
        self.ellipsis_variant = '…'

    def clean(self, text: str) -> str:
        """
        Apply full cleaning pipeline.

        Args:
            text: Raw manuscript text

        Returns:
            Cleaned text ready for TTS
        """
        text = self._remove_page_numbers(text)
        text = self._expand_abbreviations(text)
        text = self._normalize_dashes(text)
        text = self._normalize_ellipsis(text)
        text = self._strip_formatting_artifacts(text)
        text = self._normalize_whitespace(text)
        return text

    def _remove_page_numbers(self, text: str) -> str:
        """Remove page numbers (usually lines with just numbers or 'Page X')."""
        lines = text.split('\n')
        cleaned = []
        for line in lines:
            stripped = line.strip()
            # Skip lines that are just numbers or "Page X"
            if re.match(r'^\d+$', stripped) or re.match(r'^Page\s+\d+$', stripped, re.I):
                continue
            cleaned.append(line)
        return '\n'.join(cleaned)

    def _expand_abbreviations(self, text: str) -> str:
        """
        Expand common abbreviations for natural TTS.
        Context-aware for St. (could be Saint or Street).
        """
        for pattern, replacement in self.abbreviation_map.items():
            text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
        return text

    def _normalize_dashes(self, text: str) -> str:
        """Convert all em/en dash variants to single em-dash."""
        for variant in self.em_dash_variants:
            text = text.replace(variant, '—')
        return text

    def _normalize_ellipsis(self, text: str) -> str:
        """Convert ellipsis character to three dots for TTS compatibility."""
        text = text.replace(self.ellipsis_variant, '...')
        return text

    def _strip_formatting_artifacts(self, text: str) -> str:
        """Remove formatting artifacts like <i>, [sic], etc."""
        text = re.sub(r'<[^>]+>', '', text)  # HTML tags
        text = re.sub(r'\[sic\]', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\*{2,}', '**', text)  # Multiple asterisks → double
        return text

    def _normalize_whitespace(self, text: str) -> str:
        """Normalize spacing: preserve paragraph breaks, fix double spaces."""
        # Preserve double newlines (paragraph breaks)
        text = re.sub(r'\n{3,}', '\n\n', text)
        # Remove leading/trailing spaces on each line
        lines = text.split('\n')
        lines = [line.rstrip() for line in lines]
        text = '\n'.join(lines)
        # Fix double spaces within lines
        text = re.sub(r' {2,}', ' ', text)
        return text
```

---

### 3. Credits Generator

**File:** `src/parser/credits_generator.py`

Generate opening and closing credits automatically.

```python
import logging

logger = logging.getLogger(__name__)

class CreditsGenerator:
    """Generate opening and closing credits for audiobooks."""

    NARRATOR = "Kent Zimering"

    @staticmethod
    def generate_opening_credits(
        title: str,
        subtitle: Optional[str],
        author: str,
        narrator: str = NARRATOR
    ) -> str:
        """
        Generate opening credits text.

        Args:
            title: Book title
            subtitle: Book subtitle (optional)
            author: Author name
            narrator: Narrator name

        Returns:
            Opening credits text suitable for TTS

        Example output:
            "This is The Sherlock Holmes Mysteries. A complete collection.
             Written by Arthur Conan Doyle. Narrated by Kent Zimering."
        """
        parts = [f"This is {title}."]

        if subtitle:
            parts.append(f"{subtitle}.")

        parts.append(f"Written by {author}.")
        parts.append(f"Narrated by {narrator}.")

        return " ".join(parts)

    @staticmethod
    def generate_closing_credits(
        title: str,
        subtitle: Optional[str],
        author: str,
        narrator: str = NARRATOR
    ) -> str:
        """
        Generate closing credits text.

        Args:
            title: Book title
            subtitle: Book subtitle (optional)
            author: Author name
            narrator: Narrator name

        Returns:
            Closing credits text suitable for TTS

        Example output:
            "This was The Sherlock Holmes Mysteries. A complete collection.
             Written by Arthur Conan Doyle. Narrated by Kent Zimering."
        """
        parts = [f"This was {title}."]

        if subtitle:
            parts.append(f"{subtitle}.")

        parts.append(f"Written by {author}.")
        parts.append(f"Narrated by {narrator}.")

        return " ".join(parts)
```

---

## Test Data & Validation

### Test Manuscript: Sherlock Holmes

**Location:** `Formatted Manuscripts/0906-*-*-*/` (check for folder matching this pattern)

**Expected Results:**
- **Title:** "The Sherlock Holmes Mysteries" or similar
- **Author:** "Arthur Conan Doyle"
- **Chapters:** Approximately 12 short stories (e.g., "A Scandal in Bohemia", "The Red-Headed League", etc.)
- **Introduction/Preface:** Optional, should be chapter 0 if present

**Test File:** `tests/test_docx_parser.py`

```python
import pytest
from pathlib import Path
from src.parser.docx_parser import DocxParser, BookMetadata
from src.parser.text_cleaner import TextCleaner
from src.parser.credits_generator import CreditsGenerator

def test_parse_sherlock_holmes():
    """Test parsing the Sherlock Holmes manuscript."""
    parser = DocxParser()

    # Find the test manuscript
    test_docx = Path("./Formatted Manuscripts").glob("0906*/*.docx")
    test_docx = next(test_docx, None)

    assert test_docx is not None, "Sherlock Holmes manuscript not found"

    metadata, chapters = parser.parse(str(test_docx))

    # Validate metadata
    assert metadata.title is not None
    assert metadata.author is not None
    assert "Conan Doyle" in metadata.author or "Holmes" in metadata.title

    # Validate chapters
    assert len(chapters) >= 10, f"Expected 10+ chapters, got {len(chapters)}"

    # First chapter validation
    first_chapter = chapters[0]
    assert first_chapter.number >= 0
    assert first_chapter.text is not None
    assert first_chapter.word_count > 0

def test_text_cleaning():
    """Test text cleaning pipeline."""
    cleaner = TextCleaner()

    dirty = "Page 42\nDr. Watson and Mr. Holmes discussed the case—a difficult one.\nPage 43"
    clean = cleaner.clean(dirty)

    assert "Page 42" not in clean
    assert "Page 43" not in clean
    assert "Doctor Watson" in clean
    assert "Mister Holmes" in clean
    assert "—" in clean  # em-dash preserved

def test_credits_generation():
    """Test opening and closing credits generation."""
    opening = CreditsGenerator.generate_opening_credits(
        title="The Sherlock Holmes Mysteries",
        subtitle="A Complete Collection",
        author="Arthur Conan Doyle"
    )

    assert "This is The Sherlock Holmes Mysteries" in opening
    assert "A Complete Collection" in opening
    assert "Arthur Conan Doyle" in opening
    assert "Kent Zimering" in opening

    closing = CreditsGenerator.generate_closing_credits(
        title="The Sherlock Holmes Mysteries",
        subtitle="A Complete Collection",
        author="Arthur Conan Doyle"
    )

    assert "This was" in closing
```

---

## Acceptance Criteria

1. **DOCX Parser:**
   - `DocxParser.parse()` successfully reads DOCX file
   - Returns valid `BookMetadata` with title, author
   - Returns list of `Chapter` objects (at least 10+ for test book)
   - Chapter objects have: number, title, type, raw_text, word_count
   - Skip rules correctly filter out copyright, TOC, preface, thank you pages

2. **Text Cleaning:**
   - `TextCleaner.clean()` removes page numbers
   - Expands "Dr." → "Doctor", "Mr." → "Mister", etc.
   - Normalizes em-dashes to single variant
   - Normalizes ellipsis to three dots
   - Preserves paragraph structure (double newlines)

3. **Credits Generation:**
   - `CreditsGenerator.generate_opening_credits()` produces correct format
   - Opening contains: "This is", title, subtitle (if present), author, narrator
   - `CreditsGenerator.generate_closing_credits()` produces correct format
   - Closing contains: "This was", same metadata as opening

4. **Test Execution:**
   - `pytest tests/test_docx_parser.py` passes all tests
   - Successfully parses the Sherlock Holmes manuscript (0906*)
   - Detects 12+ chapters correctly
   - No import errors or missing dependencies

5. **Git Commit:**
   - All changes committed with message: `[PROMPT-02] DOCX parser and text cleaning pipeline`

---

## Additional Notes

- **Roman Numeral Conversion:** Implement or use a library (e.g., `roman` package) for I/II/III ↔ 1/2/3 conversion
- **Logging:** Use logger for debugging chapter detection, skip rule matches
- **Error Handling:** Raise clear exceptions for invalid DOCX, missing metadata, empty chapters
- **Performance:** For large manuscripts (873 books), parsing should be reasonably fast (< 5 seconds per book)
- **TOC Usage:** Extract TOC during parsing but mark as "skip_narrate"; use for validation against detected chapters

---

## Reference

- **Project Conventions:** `/sessions/focused-happy-pasteur/mnt/Coding Folder/Qwen3-TTS - Test/CLAUDE.md`
- **python-docx Documentation:** https://python-docx.readthedocs.io/
- **Test Manuscript:** Formatted Manuscripts/0906* folder
