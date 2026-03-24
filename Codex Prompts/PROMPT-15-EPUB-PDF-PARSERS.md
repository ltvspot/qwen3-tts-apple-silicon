# PROMPT-15: EPUB & PDF Fallback Parsers

**Objective:** Add EPUB and PDF parsing support as fallbacks for manuscript formats when DOCX is unavailable, with consistent output that matches the DOCX parser.

**Owner:** Codex
**Reference:** CLAUDE.md, PROJECT-STATE.md, PROMPT-02 (DOCX Manuscript Parser)

---

## Scope

### EPUB Parser

#### File: `src/parser/epub_parser.py`

**Implementation:**
```python
from pathlib import Path
from typing import List, Tuple, Optional
import ebooklib
from ebooklib import epub
import logging
from html.parser import HTMLParser

logger = logging.getLogger(__name__)

class EPUBParser:
    """
    Parse EPUB files and extract chapters following same rules as DOCX parser.
    """

    # Skip rules (same as DOCX parser from PROMPT-02)
    SKIP_SECTIONS = {
        'title page',
        'copyright',
        'table of contents',
        'toc',
        'preface',
        'message to the reader',
        'thank you for reading',
        'foreword',
        'afterword'
    }

    HEADING_TAGS = {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}

    def __init__(self):
        pass

    def parse(self, epub_path: str) -> Tuple[dict, List[dict]]:
        """
        Parse EPUB file and extract book metadata and chapters.

        Args:
            epub_path: Path to EPUB file

        Returns:
            Tuple of (book_metadata, chapters_list)

        book_metadata:
            {
                'title': str,
                'author': str,
                'subtitle': Optional[str],
                'word_count': int,
                'total_chapters': int
            }

        chapters_list: List of dicts
            {
                'chapter_n': int,
                'title': str,
                'text': str,
                'word_count': int
            }
        """
        try:
            book = epub.read_epub(epub_path)
        except Exception as e:
            raise ValueError(f"Failed to read EPUB file: {e}")

        # Extract metadata
        metadata = self._extract_metadata(book)

        # Extract chapters from spine
        chapters = self._extract_chapters(book)

        # Calculate word counts
        metadata['word_count'] = sum(ch['word_count'] for ch in chapters)
        metadata['total_chapters'] = len(chapters)

        logger.info(f"Parsed EPUB: {metadata['title']} by {metadata['author']}")
        logger.info(f"  {metadata['total_chapters']} chapters, {metadata['word_count']} words")

        return metadata, chapters

    def _extract_metadata(self, book: epub.EpubBook) -> dict:
        """Extract book metadata from EPUB."""
        title = book.get_metadata('DC', 'title')
        title = title[0][0] if title else 'Unknown'

        author = book.get_metadata('DC', 'creator')
        author = author[0][0] if author else 'Unknown'

        subtitle = book.get_metadata('DC', 'description')
        subtitle = subtitle[0][0] if subtitle else None

        return {
            'title': title,
            'author': author,
            'subtitle': subtitle,
            'word_count': 0,  # Calculated later
            'total_chapters': 0  # Calculated later
        }

    def _extract_chapters(self, book: epub.EpubBook) -> List[dict]:
        """
        Extract chapters from EPUB spine.

        Process:
        1. Iterate through spine (document reading order)
        2. Parse HTML content
        3. Detect chapter headings (H1-H6 tags)
        4. Group text under headings
        5. Apply skip rules
        """
        chapters = []
        chapter_n = 0

        for item in book.spine:
            if not isinstance(item[0], epub.EpubHtml):
                continue

            document = item[0]
            content = document.content

            # Parse HTML
            parser = EPUBHTMLParser()
            parser.feed(content.decode('utf-8', errors='ignore'))

            # Process parsed content
            for section in parser.sections:
                if self._should_skip_section(section['heading']):
                    continue

                # Clean text
                text = self._clean_text(section['text'])
                word_count = len(text.split())

                if word_count > 0:  # Skip empty chapters
                    chapters.append({
                        'chapter_n': chapter_n,
                        'title': section['heading'] or f"Chapter {chapter_n}",
                        'text': text,
                        'word_count': word_count
                    })
                    chapter_n += 1

        return chapters

    def _should_skip_section(self, heading: str) -> bool:
        """Check if section should be skipped based on heading text."""
        if not heading:
            return False

        heading_lower = heading.lower()
        for skip_phrase in self.SKIP_SECTIONS:
            if skip_phrase in heading_lower:
                return True
        return False

    def _clean_text(self, text: str) -> str:
        """
        Clean extracted text (same as DOCX parser).

        1. Normalize whitespace
        2. Remove extra blank lines
        3. Preserve paragraph structure
        """
        # Normalize whitespace
        lines = text.split('\n')
        lines = [line.strip() for line in lines]
        lines = [line for line in lines if line]  # Remove empty lines

        # Rejoin
        text = '\n'.join(lines)

        # Normalize spaces
        text = ' '.join(text.split())

        return text


class EPUBHTMLParser(HTMLParser):
    """Parse HTML content from EPUB and extract sections."""

    def __init__(self):
        super().__init__()
        self.sections = []
        self.current_heading = None
        self.current_text = []
        self.current_tag = None

    def handle_starttag(self, tag: str, attrs: list):
        """Handle opening tags."""
        if tag in {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}:
            # New heading: save previous section and start new one
            if self.current_heading or self.current_text:
                self._save_section()

            self.current_heading = None
            self.current_tag = tag

        elif tag == 'p':
            self.current_tag = 'p'

        elif tag in {'br', 'hr'}:
            self.current_text.append('\n')

    def handle_endtag(self, tag: str):
        """Handle closing tags."""
        if tag in {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}:
            self.current_tag = None

        elif tag == 'p':
            if self.current_text and not self.current_text[-1].endswith('\n'):
                self.current_text.append('\n')
            self.current_tag = None

    def handle_data(self, data: str):
        """Handle text data."""
        if data.strip():
            if self.current_tag and self.current_tag.startswith('h'):
                if self.current_heading is None:
                    self.current_heading = data.strip()
                else:
                    self.current_heading += ' ' + data.strip()
            else:
                self.current_text.append(data.strip())

    def _save_section(self):
        """Save current section to sections list."""
        if self.current_heading or self.current_text:
            text = ' '.join(self.current_text).strip()
            self.sections.append({
                'heading': self.current_heading or '',
                'text': text
            })

        self.current_heading = None
        self.current_text = []
```

### PDF Parser

#### File: `src/parser/pdf_parser.py`

**Implementation:**
```python
from pathlib import Path
from typing import List, Tuple, Optional
import pdfplumber
import re
import logging

logger = logging.getLogger(__name__)

class PDFParser:
    """
    Parse PDF files and extract chapters using heuristic heading detection.
    """

    # Skip rules (same as DOCX parser)
    SKIP_SECTIONS = {
        'title page',
        'copyright',
        'table of contents',
        'toc',
        'preface',
        'message to the reader',
        'thank you for reading',
        'foreword',
        'afterword'
    }

    # Heuristic chapter patterns
    CHAPTER_PATTERNS = [
        r'^[Cc]hapter\s+[\dIVXLCDM]+',  # Chapter I, Chapter 1, etc.
        r'^[Pp]art\s+[\dIVXLCDM]+',  # Part I, Part 1, etc.
        r'^[\d]+\.',  # Just a number: 1. 2. 3.
        r'^[IVXLCDM]+\.',  # Roman numerals: I. II. III.
    ]

    def __init__(self):
        pass

    def parse(self, pdf_path: str) -> Tuple[dict, List[dict]]:
        """
        Parse PDF file and extract book metadata and chapters.

        Args:
            pdf_path: Path to PDF file

        Returns:
            Tuple of (book_metadata, chapters_list)
        """
        try:
            with pdfplumber.open(pdf_path) as pdf:
                # Extract metadata
                metadata = self._extract_metadata(pdf)

                # Extract chapters
                chapters = self._extract_chapters(pdf)

        except Exception as e:
            raise ValueError(f"Failed to read PDF file: {e}")

        # Calculate word counts
        metadata['word_count'] = sum(ch['word_count'] for ch in chapters)
        metadata['total_chapters'] = len(chapters)

        logger.info(f"Parsed PDF: {metadata['title']}")
        logger.info(f"  {metadata['total_chapters']} chapters, {metadata['word_count']} words")

        return metadata, chapters

    def _extract_metadata(self, pdf) -> dict:
        """Extract metadata from PDF."""
        metadata = pdf.metadata or {}

        title = metadata.get('/Title', 'Unknown')
        author = metadata.get('/Author', 'Unknown')
        subtitle = metadata.get('/Subject')

        # Try to extract from first page if metadata missing
        if title == 'Unknown' and len(pdf.pages) > 0:
            first_page_text = pdf.pages[0].extract_text()
            lines = first_page_text.split('\n')
            title = lines[0].strip() if lines else 'Unknown'

        return {
            'title': title,
            'author': author,
            'subtitle': subtitle,
            'word_count': 0,
            'total_chapters': 0
        }

    def _extract_chapters(self, pdf) -> List[dict]:
        """
        Extract chapters from PDF using heuristic heading detection.

        Process:
        1. Extract text from all pages
        2. Detect chapter boundaries (large font, after page breaks)
        3. Match chapter patterns (Chapter I, Chapter 1, etc.)
        4. Group text between chapters
        5. Apply skip rules
        """
        all_text = ''
        chapter_starts = {}  # Map chapter heading to text position

        # Extract text from all pages
        for page_num, page in enumerate(pdf.pages):
            try:
                # Check if page has significant size change (potential chapter start)
                text = page.extract_text()
                all_text += text + '\n'

                # Detect chapter headings on this page
                lines = text.split('\n')
                for line in lines:
                    if self._is_chapter_heading(line):
                        char_pos = len(all_text) - len(text)
                        chapter_starts[line.strip()] = char_pos

            except Exception as e:
                logger.warning(f"Failed to extract text from page {page_num}: {e}")

        # Split text into chapters based on detected headings
        chapters = []
        chapter_n = 0
        sorted_headings = sorted(chapter_starts.items(), key=lambda x: x[1])

        for i, (heading, start_pos) in enumerate(sorted_headings):
            # Skip this section?
            if self._should_skip_section(heading):
                continue

            # Get text until next chapter
            end_pos = sorted_headings[i + 1][1] if i + 1 < len(sorted_headings) else len(all_text)
            text = all_text[start_pos:end_pos]

            # Clean and extract
            text = self._clean_text(text)
            word_count = len(text.split())

            if word_count > 0:
                chapters.append({
                    'chapter_n': chapter_n,
                    'title': heading or f"Chapter {chapter_n}",
                    'text': text,
                    'word_count': word_count
                })
                chapter_n += 1

        return chapters

    def _is_chapter_heading(self, line: str) -> bool:
        """Check if line matches chapter heading patterns."""
        line = line.strip()

        # Too short or too long
        if len(line) < 3 or len(line) > 200:
            return False

        # Check against patterns
        for pattern in self.CHAPTER_PATTERNS:
            if re.match(pattern, line):
                return True

        return False

    def _should_skip_section(self, heading: str) -> bool:
        """Check if section should be skipped."""
        if not heading:
            return False

        heading_lower = heading.lower()
        for skip_phrase in self.SKIP_SECTIONS:
            if skip_phrase in heading_lower:
                return True
        return False

    def _clean_text(self, text: str) -> str:
        """Clean extracted text (same as DOCX parser)."""
        lines = text.split('\n')
        lines = [line.strip() for line in lines]
        lines = [line for line in lines if line]

        text = '\n'.join(lines)
        text = ' '.join(text.split())

        return text
```

### Parser Factory & Auto-Detection

#### File: `src/parser/__init__.py` (MODIFIED)

```python
from pathlib import Path
from typing import List, Tuple, Optional
from .docx_parser import DOCXParser
from .epub_parser import EPUBParser
from .pdf_parser import PDFParser
import logging

logger = logging.getLogger(__name__)

class ManuscriptParserFactory:
    """
    Factory for selecting the best parser based on available manuscript files.
    Priority: DOCX > EPUB > PDF
    """

    @staticmethod
    def get_parser(manuscript_folder: str) -> Optional[tuple]:
        """
        Detect best available format and return parser + file path.

        Args:
            manuscript_folder: Path to manuscript folder

        Returns:
            Tuple of (parser_instance, file_path) or (None, None) if no valid format
        """
        folder = Path(manuscript_folder)

        if not folder.exists():
            logger.error(f"Manuscript folder not found: {manuscript_folder}")
            return None, None

        # Check for DOCX (primary format)
        docx_files = list(folder.glob('*.docx'))
        if docx_files:
            logger.info(f"Using DOCX parser for {folder.name}")
            return DOCXParser(), str(docx_files[0])

        # Check for EPUB (fallback)
        epub_files = list(folder.glob('*.epub'))
        if epub_files:
            logger.info(f"Using EPUB parser for {folder.name}")
            return EPUBParser(), str(epub_files[0])

        # Check for PDF (fallback)
        pdf_files = list(folder.glob('*.pdf'))
        if pdf_files:
            logger.info(f"Using PDF parser for {folder.name}")
            return PDFParser(), str(pdf_files[0])

        logger.warning(f"No supported format found in {folder.name}")
        return None, None

    @staticmethod
    def parse_manuscript(manuscript_folder: str) -> Tuple[dict, List[dict]]:
        """
        Auto-detect format and parse manuscript folder.

        Returns: (book_metadata, chapters_list)
        """
        parser, file_path = ManuscriptParserFactory.get_parser(manuscript_folder)

        if parser is None or file_path is None:
            raise ValueError(f"No supported manuscript format in {manuscript_folder}")

        return parser.parse(file_path)
```

### Parser API Integration

#### File: `src/api/parser_routes.py` (MODIFIED from PROMPT-03)

Update the POST /api/book/{id}/parse endpoint to use format auto-detection:

```python
@router.post("/book/{id}/parse")
async def parse_manuscript(id: int) -> dict:
    """
    Parse manuscript for a book (auto-detect format).

    Uses DOCX > EPUB > PDF priority.
    """
    from src.parser import ManuscriptParserFactory

    book = db.query(Book).filter(Book.id == id).first()
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")

    manuscript_folder = book.manuscript_path  # e.g., "Formatted Manuscripts/The Count of Monte Cristo"

    try:
        # Auto-detect format and parse
        metadata, chapters = ManuscriptParserFactory.parse_manuscript(manuscript_folder)

        # Rest of parsing logic (same as PROMPT-03)
        # Store in database, update book status, etc.

        return {
            "success": True,
            "book_id": id,
            "title": metadata['title'],
            "chapters": len(chapters),
            "word_count": metadata['word_count'],
            "message": "Manuscript parsed successfully"
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Parse error: {e}")
        raise HTTPException(status_code=500, detail="Parse failed")
```

### Testing Consistency

#### Test File: `tests/test_parser_consistency.py`

```python
import pytest
from src.parser.docx_parser import DOCXParser
from src.parser.epub_parser import EPUBParser
from src.parser.pdf_parser import PDFParser

class TestParserConsistency:
    """
    Verify that parsing the same book in different formats
    produces consistent results.
    """

    @pytest.fixture
    def test_book_path(self):
        """Return path to test manuscript folder with all 3 formats."""
        return "tests/fixtures/test_manuscript"

    def test_consistent_chapter_count(self, test_book_path):
        """All formats should detect same number of chapters."""
        docx_parser = DOCXParser()
        epub_parser = EPUBParser()
        pdf_parser = PDFParser()

        # Parse same book in all formats
        docx_meta, docx_chaps = docx_parser.parse(f"{test_book_path}/book.docx")
        epub_meta, epub_chaps = epub_parser.parse(f"{test_book_path}/book.epub")
        pdf_meta, pdf_chaps = pdf_parser.parse(f"{test_book_path}/book.pdf")

        # Should all have same chapter count
        assert len(docx_chaps) == len(epub_chaps)
        assert len(docx_chaps) == len(pdf_chaps)

    def test_consistent_metadata(self, test_book_path):
        """All formats should extract same title and author."""
        docx_parser = DOCXParser()
        epub_parser = EPUBParser()
        pdf_parser = PDFParser()

        docx_meta, _ = docx_parser.parse(f"{test_book_path}/book.docx")
        epub_meta, _ = epub_parser.parse(f"{test_book_path}/book.epub")
        pdf_meta, _ = pdf_parser.parse(f"{test_book_path}/book.pdf")

        # Title and author should match
        assert docx_meta['title'] == epub_meta['title'] == pdf_meta['title']
        assert docx_meta['author'] == epub_meta['author'] == pdf_meta['author']

    def test_consistent_text_content(self, test_book_path):
        """Chapter text should be similar across formats (within 10% word count variation)."""
        docx_parser = DOCXParser()
        epub_parser = EPUBParser()
        pdf_parser = PDFParser()

        docx_meta, docx_chaps = docx_parser.parse(f"{test_book_path}/book.docx")
        epub_meta, epub_chaps = epub_parser.parse(f"{test_book_path}/book.epub")
        pdf_meta, pdf_chaps = pdf_parser.parse(f"{test_book_path}/book.pdf")

        # Compare word counts for first 3 chapters (allow 10% variance)
        for i in range(min(3, len(docx_chaps), len(epub_chaps), len(pdf_chaps))):
            docx_words = docx_chaps[i]['word_count']
            epub_words = epub_chaps[i]['word_count']
            pdf_words = pdf_chaps[i]['word_count']

            # Allow 10% variation due to different extraction methods
            assert abs(docx_words - epub_words) / docx_words <= 0.10
            assert abs(docx_words - pdf_words) / docx_words <= 0.10
```

---

## Acceptance Criteria

### Functional Requirements - EPUB Parser
- [ ] Successfully parses EPUB files with multiple chapters
- [ ] Extracts book title, author from EPUB metadata
- [ ] Detects chapters from HTML headings (H1-H6)
- [ ] Applies skip rules (title page, copyright, TOC, etc.)
- [ ] Cleans text (normalize whitespace, remove extra blank lines)
- [ ] Calculates word count for each chapter and book
- [ ] Returns chapter list with consistent structure (chapter_n, title, text, word_count)

### Functional Requirements - PDF Parser
- [ ] Successfully parses PDF files with multiple chapters
- [ ] Detects chapter headings using heuristic patterns (Chapter I, 1., etc.)
- [ ] Extracts text from all pages
- [ ] Applies skip rules
- [ ] Cleans extracted text
- [ ] Handles PDFs with missing metadata gracefully
- [ ] Returns chapter list with consistent structure

### Functional Requirements - Format Auto-Detection
- [ ] `ManuscriptParserFactory.get_parser()` returns DOCX parser if DOCX present
- [ ] Returns EPUB parser if DOCX absent but EPUB present
- [ ] Returns PDF parser if only PDF available
- [ ] Returns (None, None) if no supported format found
- [ ] Logs which format was selected
- [ ] `parse_manuscript()` auto-detects and parses

### Functional Requirements - API Integration
- [ ] `POST /api/book/{id}/parse` works with any supported format
- [ ] Parsing updates book status, stores chapters in database
- [ ] Error handling for unsupported formats
- [ ] Proper error messages returned to client

### Code Quality
- [ ] All parsers return consistent output structure
- [ ] Proper exception handling for corrupted files
- [ ] Logging of parsing steps and format selection
- [ ] Type hints on all functions
- [ ] DRY principle: skip rules and text cleaning shared

### Testing Requirements

1. **EPUB Parser Unit Tests:**
   - [ ] `test_parse_epub_basic`: Parse valid EPUB with 3 chapters
   - [ ] `test_epub_metadata_extraction`: Extract title and author
   - [ ] `test_epub_skip_sections`: Skip copyright, TOC
   - [ ] `test_epub_heading_detection`: Detect H1-H6 headings
   - [ ] `test_epub_corrupted_file`: Handle invalid EPUB
   - [ ] `test_epub_empty_chapters`: Skip chapters with no text

2. **PDF Parser Unit Tests:**
   - [ ] `test_parse_pdf_basic`: Parse valid PDF with chapters
   - [ ] `test_pdf_chapter_pattern_detection`: Detect "Chapter I", "1.", etc.
   - [ ] `test_pdf_skip_sections`: Skip preface, foreword
   - [ ] `test_pdf_missing_metadata`: Handle PDF without title
   - [ ] `test_pdf_corrupted_file`: Handle invalid PDF
   - [ ] `test_pdf_large_document`: Handle 500+ page PDF

3. **Format Auto-Detection Tests:**
   - [ ] `test_priority_docx_over_epub`: Choose DOCX if both present
   - [ ] `test_priority_epub_over_pdf`: Choose EPUB if DOCX missing
   - [ ] `test_fallback_to_pdf`: Choose PDF if DOCX/EPUB missing
   - [ ] `test_no_format_found`: Return None if no supported format
   - [ ] `test_logging`: Verify format selection logged

4. **Parser Consistency Tests:**
   - [ ] `test_consistent_chapter_count`: Same count across formats
   - [ ] `test_consistent_metadata`: Same title/author across formats
   - [ ] `test_consistent_text_content`: Word counts within 10% variance
   - [ ] `test_same_skip_rules`: All formats skip same sections

5. **API Integration Tests:**
   - [ ] Parse book with DOCX format
   - [ ] Parse book with EPUB format
   - [ ] Parse book with PDF format
   - [ ] Auto-detection selects correct parser
   - [ ] Error response for missing manuscript

6. **Manual Testing Scenario:**
   - [ ] Create test folder with all 3 formats of same book
   - [ ] Parse via API → verify DOCX used
   - [ ] Remove DOCX → parse → verify EPUB used
   - [ ] Remove EPUB → parse → verify PDF used
   - [ ] Compare parsed chapters across formats
   - [ ] Verify chapter count, metadata, text consistency
   - [ ] Test with actual manuscripts from 873 library

---

## File Structure

```
src/
  parser/
    docx_parser.py                    # EXISTING
    epub_parser.py                    # NEW: EPUB parsing
    pdf_parser.py                     # NEW: PDF parsing
    __init__.py                       # MODIFIED: Factory and auto-detection
  api/
    parser_routes.py                  # MODIFIED: Update parse endpoint

tests/
  test_epub_parser.py                 # NEW: EPUB parser tests
  test_pdf_parser.py                  # NEW: PDF parser tests
  test_parser_consistency.py          # NEW: Cross-format consistency
  fixtures/
    test_manuscript/
      book.docx                       # Test manuscript (all 3 formats)
      book.epub
      book.pdf
```

---

## Implementation Notes

### EPUB Structure
- EPUB files are ZIP archives with standardized structure
- Manifest: list of all content documents
- Spine: reading order of chapters
- Parse HTML content from spine in order

### PDF Text Extraction
- PDFs may have text as images (scanned books) — skip these
- Use `pdfplumber` for structured text extraction
- Text may be out of order depending on PDF creation method
- Heuristic chapter detection helps reconstruct chapter boundaries

### Parser Priority Rationale
1. **DOCX:** Most structured format, most reliable parsing
2. **EPUB:** Standard for digital books, good formatting preservation
3. **PDF:** Least structured, prone to extraction errors, but widely available

### Consistency Expectations
- Chapter count may vary ±1 due to different content detection
- Word counts may vary ±10% due to extraction method differences
- Metadata (title, author) should match exactly
- Skip rules consistently applied

### Error Handling Strategy
- Corrupted file → Raise ValueError with format info
- Missing chapters → Warn but continue
- Extraction failure → Fallback to empty text
- Missing metadata → Use defaults ("Unknown")

---

## References

- CLAUDE.md § Skip Rules, Manuscript Structure
- PROMPT-02: DOCX Manuscript Parser (parser interface)
- PROMPT-03: Parser API Integration (API contract)
- ebooklib: https://github.com/aerkalov/ebooklib
- pdfplumber: https://github.com/jsvine/pdfplumber

---

## Commit Message

```
[PROMPT-15] Add EPUB and PDF fallback parsers

- Create EPUBParser with heading detection and HTML parsing
- Create PDFParser with heuristic chapter detection
- Implement ManuscriptParserFactory with format auto-detection
- Priority order: DOCX > EPUB > PDF
- Apply same skip rules and text cleaning across all parsers
- Update parse API endpoint to use auto-detection
- Comprehensive consistency tests across formats
```

---

**Status:** READY FOR IMPLEMENTATION
**Estimated Effort:** 10-12 hours (parsers, factory, testing, consistency verification)
**Dependencies:** PROMPT-02 (DOCX parser reference), PROMPT-03 (API integration)
