# PROMPT-03: Parser API & Library Scanner

**Objective:** Create FastAPI endpoints for library scanning, book discovery, manuscript parsing, and chapter text management.

**Project Context:** See `/sessions/focused-happy-pasteur/mnt/Coding Folder/Qwen3-TTS - Test/CLAUDE.md` for conventions and architecture.

---

## Scope & Requirements

### 1. Library Scanner

**File:** `src/api/library.py`

Create a scanner that reads the `Formatted Manuscripts/` directory and indexes all books.

```python
import logging
import re
from pathlib import Path
from typing import List, Dict, Optional
from sqlalchemy.orm import Session
from src.database import Book, Chapter
from src.config import FORMATTED_MANUSCRIPTS_PATH

logger = logging.getLogger(__name__)

class LibraryScanner:
    """Scan Formatted Manuscripts directory and index books."""

    # Folder name pattern: {ID}-{Title}-{TrimSize}-{PageCount}
    # Example: 0906-The Sherlock Holmes Mysteries-6x9-320
    FOLDER_PATTERN = r'^(\d+)-(.+?)-([0-9.x]+)-(\d+)$'

    def __init__(self, manuscripts_path: str = FORMATTED_MANUSCRIPTS_PATH):
        """Initialize scanner with path to Formatted Manuscripts."""
        self.manuscripts_path = Path(manuscripts_path)

    def scan(self, db_session: Session) -> Dict[str, any]:
        """
        Scan library and update database.

        Args:
            db_session: SQLAlchemy session

        Returns:
            Dict with keys:
            - total_found: int, number of folders scanned
            - total_indexed: int, number successfully added to DB
            - errors: List[str], any parsing errors
            - new_books: int, newly added books (not duplicates)

        Process:
        1. List all folders in Formatted Manuscripts/
        2. For each folder, extract metadata from folder name
        3. Check if book already exists in DB (by folder_path)
        4. If new, create Book record with status="not_started"
        5. Return summary
        """
        ...

    def _parse_folder_name(self, folder_name: str) -> Optional[Dict]:
        """
        Parse folder name into book metadata.

        Args:
            folder_name: Folder name, e.g., "0906-The Sherlock Holmes Mysteries-6x9-320"

        Returns:
            Dict with keys: id, title, trim_size, page_count
            or None if format invalid

        Example:
            _parse_folder_name("0906-The Sherlock Holmes Mysteries-6x9-320")
            → {"id": "0906", "title": "The Sherlock Holmes Mysteries", "trim_size": "6x9", "page_count": 320}
        """
        ...

    def _find_docx_file(self, folder_path: Path) -> Optional[Path]:
        """
        Find a DOCX file in folder.
        Return first DOCX found, or None if none exists.
        """
        ...
```

---

### 2. FastAPI Routes

**File:** `src/api/routes.py`

Create API endpoints for library and book operations. Register these in `src/main.py` using `app.include_router()`.

```python
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel
import logging

logger = logging.getLogger(__name__)

# ============================================================================
# Pydantic Models (Request/Response)
# ============================================================================

class BookResponse(BaseModel):
    """Response model for a book."""
    id: int
    title: str
    subtitle: Optional[str]
    author: str
    narrator: str
    folder_path: str
    status: str
    page_count: Optional[int]
    trim_size: Optional[str]
    chapter_count: int  # computed from DB
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True

class ChapterResponse(BaseModel):
    """Response model for a chapter."""
    id: int
    book_id: int
    number: int
    title: Optional[str]
    type: str
    text_content: Optional[str]
    word_count: Optional[int]
    status: str
    audio_path: Optional[str]
    duration_seconds: Optional[float]
    qa_status: Optional[str]
    qa_notes: Optional[str]
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True

class LibraryScanResponse(BaseModel):
    """Response model for library scan."""
    total_found: int
    total_indexed: int
    new_books: int
    errors: List[str]

class ParseBookRequest(BaseModel):
    """Request to parse a book DOCX file."""
    overwrite: bool = False  # If true, re-parse even if already parsed

class ChapterUpdateRequest(BaseModel):
    """Request to update chapter text."""
    text_content: str

# ============================================================================
# Router Setup
# ============================================================================

router = APIRouter(prefix="/api", tags=["library"])

def get_db() -> Session:
    """Dependency for database session."""
    # Implementation in main.py
    ...

# ============================================================================
# Endpoints
# ============================================================================

@router.post("/library/scan")
async def scan_library(db: Session = Depends(get_db)) -> LibraryScanResponse:
    """
    Scan the Formatted Manuscripts/ folder and index all books.

    Returns:
        LibraryScanResponse with counts of found, indexed, new books, and any errors

    Process:
    - Use LibraryScanner to scan all folders
    - Add new books to database
    - Return summary statistics
    """
    scanner = LibraryScanner()
    result = scanner.scan(db)
    db.commit()
    return LibraryScanResponse(**result)

@router.get("/library")
async def get_library(
    status_filter: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db)
) -> Dict:
    """
    Get all books in library with filtering.

    Query Parameters:
    - status_filter: Optional, filter by status (e.g., "parsed", "generating")
    - limit: Results per page (default 100)
    - offset: Pagination offset

    Returns:
        {
            "total": int,
            "books": List[BookResponse],
            "stats": {
                "not_started": int,
                "parsed": int,
                "generating": int,
                "generated": int,
                "qa": int,
                "qa_approved": int,
                "exported": int
            }
        }
    """
    ...

@router.get("/book/{book_id}")
async def get_book(book_id: int, db: Session = Depends(get_db)) -> BookResponse:
    """
    Get a single book by ID.

    Returns:
        BookResponse with full metadata
    """
    book = db.query(Book).filter(Book.id == book_id).first()
    if not book:
        raise HTTPException(status_code=404, detail=f"Book {book_id} not found")
    return BookResponse.from_orm(book)

@router.get("/book/{book_id}/chapters")
async def get_book_chapters(
    book_id: int,
    db: Session = Depends(get_db)
) -> List[ChapterResponse]:
    """
    Get all chapters for a book, ordered by chapter number.

    Returns:
        List of ChapterResponse objects
    """
    book = db.query(Book).filter(Book.id == book_id).first()
    if not book:
        raise HTTPException(status_code=404, detail=f"Book {book_id} not found")

    chapters = db.query(Chapter).filter(
        Chapter.book_id == book_id
    ).order_by(Chapter.number).all()

    return [ChapterResponse.from_orm(ch) for ch in chapters]

@router.post("/book/{book_id}/parse")
async def parse_book(
    book_id: int,
    request: ParseBookRequest,
    db: Session = Depends(get_db)
) -> Dict:
    """
    Trigger parsing of a book's DOCX file.

    Args:
        book_id: Book ID
        request: ParseBookRequest with optional overwrite flag

    Returns:
        {
            "status": "parsing" | "already_parsed",
            "chapters_detected": int,
            "message": str
        }

    Process:
    1. Check if book exists
    2. If already parsed and overwrite=False, return "already_parsed"
    3. Find DOCX file in book's folder
    4. Use DocxParser to parse
    5. Clear existing chapters if overwrite=True
    6. Insert parsed chapters into DB (including opening/closing credits)
    7. Update book status to "parsed"
    8. Return summary
    """
    from src.parser.docx_parser import DocxParser
    from src.parser.credits_generator import CreditsGenerator

    book = db.query(Book).filter(Book.id == book_id).first()
    if not book:
        raise HTTPException(status_code=404, detail=f"Book {book_id} not found")

    # Check if already parsed
    if book.status == "parsed" and not request.overwrite:
        return {
            "status": "already_parsed",
            "chapters_detected": len(book.chapters),
            "message": "Book already parsed. Set overwrite=True to re-parse."
        }

    # Find DOCX file
    manuscripts_path = Path(FORMATTED_MANUSCRIPTS_PATH) / book.folder_path
    docx_files = list(manuscripts_path.glob("*.docx"))
    if not docx_files:
        raise HTTPException(
            status_code=400,
            detail=f"No DOCX file found in {book.folder_path}"
        )

    # Parse DOCX
    parser = DocxParser()
    metadata, chapters_data = parser.parse(str(docx_files[0]))

    # Update book metadata if not already set
    if not book.author:
        book.author = metadata.author
    if not book.title:
        book.title = metadata.title
    if not book.subtitle and metadata.subtitle:
        book.subtitle = metadata.subtitle

    # Clear existing chapters if overwrite
    if request.overwrite:
        db.query(Chapter).filter(Chapter.book_id == book_id).delete()

    # Create opening credits chapter
    opening = CreditsGenerator.generate_opening_credits(
        book.title,
        book.subtitle,
        book.author
    )
    opening_ch = Chapter(
        book_id=book_id,
        number=0,
        title="Opening Credits",
        type="opening_credits",
        text_content=opening,
        word_count=len(opening.split()),
        status="pending"
    )
    db.add(opening_ch)

    # Create chapters from parsed data
    for parsed_ch in chapters_data:
        chapter = Chapter(
            book_id=book_id,
            number=parsed_ch.number,
            title=parsed_ch.title,
            type=parsed_ch.type,
            text_content=parsed_ch.raw_text,
            word_count=parsed_ch.word_count,
            status="pending"
        )
        db.add(chapter)

    # Create closing credits chapter
    closing = CreditsGenerator.generate_closing_credits(
        book.title,
        book.subtitle,
        book.author
    )
    closing_ch = Chapter(
        book_id=book_id,
        number=len(chapters_data) + 1,
        title="Closing Credits",
        type="closing_credits",
        text_content=closing,
        word_count=len(closing.split()),
        status="pending"
    )
    db.add(closing_ch)

    book.status = "parsed"
    db.commit()

    logger.info(f"Parsed book {book_id}: {len(chapters_data)} chapters")

    return {
        "status": "parsing",
        "chapters_detected": len(chapters_data) + 2,  # +2 for opening/closing
        "message": f"Successfully parsed {len(chapters_data)} chapters plus opening/closing credits"
    }

@router.get("/book/{book_id}/parsed")
async def get_parsed_chapters(
    book_id: int,
    db: Session = Depends(get_db)
) -> List[ChapterResponse]:
    """
    Get parsed chapter data as JSON (includes text_content).

    Returns:
        List of ChapterResponse objects with full text
    """
    book = db.query(Book).filter(Book.id == book_id).first()
    if not book:
        raise HTTPException(status_code=404, detail=f"Book {book_id} not found")

    if book.status != "parsed":
        raise HTTPException(
            status_code=400,
            detail=f"Book not yet parsed. Status: {book.status}"
        )

    chapters = db.query(Chapter).filter(
        Chapter.book_id == book_id
    ).order_by(Chapter.number).all()

    return [ChapterResponse.from_orm(ch) for ch in chapters]

@router.put("/book/{book_id}/chapter/{chapter_number}/text")
async def update_chapter_text(
    book_id: int,
    chapter_number: int,
    request: ChapterUpdateRequest,
    db: Session = Depends(get_db)
) -> ChapterResponse:
    """
    Update the text of a parsed chapter (for corrections before generation).

    Args:
        book_id: Book ID
        chapter_number: Chapter number
        request: ChapterUpdateRequest with new text_content

    Returns:
        Updated ChapterResponse

    Updates:
    - text_content
    - word_count
    - updated_at timestamp
    """
    chapter = db.query(Chapter).filter(
        Chapter.book_id == book_id,
        Chapter.number == chapter_number
    ).first()

    if not chapter:
        raise HTTPException(
            status_code=404,
            detail=f"Chapter {chapter_number} not found in book {book_id}"
        )

    chapter.text_content = request.text_content
    chapter.word_count = len(request.text_content.split())
    db.commit()

    logger.info(f"Updated chapter {chapter_number} of book {book_id}")

    return ChapterResponse.from_orm(chapter)
```

---

## Integration in main.py

Add to `src/main.py`:

```python
from src.api.routes import router as api_router

app.include_router(api_router)
```

---

## Tests

**File:** `tests/test_library_api.py`

```python
import pytest
from fastapi.testclient import TestClient
from pathlib import Path
from src.main import app
from src.database import Session, Book

client = TestClient(app)

def test_scan_library(test_db):
    """Test library scanning."""
    response = client.post("/api/library/scan")
    assert response.status_code == 200
    data = response.json()
    assert "total_found" in data
    assert "total_indexed" in data
    assert data["total_found"] >= 0

def test_get_library(test_db):
    """Test retrieving library."""
    response = client.get("/api/library")
    assert response.status_code == 200
    data = response.json()
    assert "total" in data
    assert "books" in data
    assert "stats" in data

def test_get_book(test_db):
    """Test retrieving a single book."""
    # Create a test book
    book = Book(
        title="Test Book",
        author="Test Author",
        folder_path="test-folder",
        status="not_started"
    )
    test_db.add(book)
    test_db.commit()

    response = client.get(f"/api/book/{book.id}")
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Test Book"
    assert data["author"] == "Test Author"

def test_parse_book(test_db):
    """Test parsing a book (requires actual DOCX file)."""
    # This test would require a real DOCX file in Formatted Manuscripts/
    # Skip for now, will work once parser is implemented
    pass

def test_update_chapter_text(test_db):
    """Test updating chapter text."""
    # Create test book and chapter
    book = Book(title="Test", author="Author", folder_path="test", status="parsed")
    test_db.add(book)
    test_db.commit()

    from src.database import Chapter
    chapter = Chapter(
        book_id=book.id,
        number=1,
        title="Chapter 1",
        type="chapter",
        text_content="Original text",
        word_count=2
    )
    test_db.add(chapter)
    test_db.commit()

    response = client.put(
        f"/api/book/{book.id}/chapter/1/text",
        json={"text_content": "Updated text"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["text_content"] == "Updated text"
    assert data["word_count"] == 2
```

---

## Acceptance Criteria

1. **Library Scanner:**
   - `LibraryScanner.scan()` reads all folders in Formatted Manuscripts/
   - Correctly parses folder names (ID-Title-TrimSize-PageCount)
   - Creates Book records in database
   - Returns accurate counts in summary

2. **Scan Endpoint:**
   - `POST /api/library/scan` returns 200 with LibraryScanResponse
   - After first scan, database contains 873+ book records
   - Subsequent scans don't create duplicates

3. **Library Retrieval:**
   - `GET /api/library` returns all books with pagination
   - Response includes status counts
   - Filtering by status works

4. **Book Detail:**
   - `GET /api/book/{id}` returns single book metadata
   - Includes chapter count
   - Returns 404 for invalid ID

5. **Book Parsing:**
   - `POST /api/book/{id}/parse` successfully parses DOCX files
   - Creates Chapter records including opening/closing credits
   - Updates book status to "parsed"
   - Returns chapter count

6. **Chapter Management:**
   - `GET /api/book/{id}/chapters` returns ordered chapter list
   - `PUT /api/book/{id}/chapter/{n}/text` updates text and word count
   - `GET /api/book/{id}/parsed` returns full chapter data

7. **Tests:**
   - `pytest tests/test_library_api.py` passes all tests
   - No import errors or missing dependencies

8. **Git Commit:**
   - All changes committed with message: `[PROMPT-03] Parser API and library scanner`

---

## Reference

- **Project Conventions:** `/sessions/focused-happy-pasteur/mnt/Coding Folder/Qwen3-TTS - Test/CLAUDE.md`
- **FastAPI Docs:** https://fastapi.tiangolo.com/
- **SQLAlchemy ORM:** https://docs.sqlalchemy.org/
