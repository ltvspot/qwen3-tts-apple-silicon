# PROMPT-19: Critical Bug Fixes — 6 Verified Production Bugs

## Overview

A comprehensive QA audit identified 6 bugs (3 P1, 1 P2, 1 P3) that must be fixed before production use. All 6 have been verified against the code. This prompt fixes every one of them.

**Critical: all existing tests (152 backend, 28 frontend) must continue to pass. Add new tests for every fix.**

---

## BUG 1 (P1): Single chapter generation promotes entire book to "generated"

### Problem
In `src/pipeline/queue_manager.py` around line 952-955, when ANY single chapter job completes, the book status is unconditionally set to `GENERATED`:
```python
if book is not None:
    book.status = BookStatus.GENERATED
    book.generation_status = BookGenerationStatus.IDLE
    book.generation_eta_seconds = 0
```
This means generating just 1 chapter out of 20 marks the entire book as "generated".

### Fix
In `_finalize_job()`, before setting `book.status = BookStatus.GENERATED`, check whether ALL chapters in the book have been generated:

```python
if book is not None:
    # Only promote to GENERATED if ALL chapters are done
    total_chapters = db_session.query(Chapter).filter(
        Chapter.book_id == book.id
    ).count()
    generated_chapters = db_session.query(Chapter).filter(
        Chapter.book_id == book.id,
        Chapter.status == ChapterStatus.GENERATED
    ).count()

    if generated_chapters >= total_chapters and total_chapters > 0:
        book.status = BookStatus.GENERATED
    else:
        # Keep as PARSED (or a new PARTIALLY_GENERATED status if one exists)
        # At minimum, don't promote to GENERATED
        pass

    book.generation_status = BookGenerationStatus.IDLE
    book.generation_eta_seconds = 0
```

Also, if the job failed (`failed=True`), do NOT promote the book to GENERATED regardless.

### New tests
- `test_single_chapter_generation_does_not_promote_book` — Generate 1 chapter of a 5-chapter book, verify book.status is NOT GENERATED
- `test_all_chapters_generated_promotes_book` — Generate all chapters, verify book.status IS GENERATED
- `test_failed_generation_does_not_promote_book` — Failed job should never set book to GENERATED

---

## BUG 2 (P1): Export accepts partial books and marks them exported

### Problem
In `src/api/export_routes.py` around line 181 (`_queue_export_for_book`), there is no readiness gate. A book with only 1 out of 20 chapters generated can be exported. The exporter at `src/pipeline/exporter.py` line 946 then marks the book as `EXPORTED` even though most chapters are missing.

### Fix

**2A. Add readiness gate in export_routes.py:**

Before queuing an export, verify the book is ready:

```python
async def _validate_book_export_readiness(book_id: int, db: Session, include_only_approved: bool = False) -> tuple[bool, str]:
    """Check if a book is ready for export. Returns (ready, reason)."""
    book = db.query(Book).filter(Book.id == book_id).first()
    if not book:
        return False, "Book not found"

    chapters = db.query(Chapter).filter(Chapter.book_id == book_id).all()
    if not chapters:
        return False, "Book has no chapters. Parse it first."

    total = len(chapters)
    generated = sum(1 for c in chapters if c.status == ChapterStatus.GENERATED)

    if generated < total:
        return False, f"Only {generated}/{total} chapters generated. Generate all chapters before exporting."

    if include_only_approved:
        approved = sum(1 for c in chapters if c.qa_status in [QAStatus.APPROVED, QAStatus.AUTO_APPROVED])
        if approved < total:
            return False, f"Only {approved}/{total} chapters approved. Approve all chapters before exporting."

    return True, ""
```

Call this BEFORE `_queue_export_for_book`. Return 400 with the reason if not ready.

**2B. Add readiness check in the export endpoint:**

In the export trigger endpoint, add:
```python
ready, reason = await _validate_book_export_readiness(book_id, db, include_only_approved=require_approval)
if not ready:
    raise HTTPException(status_code=400, detail=reason)
```

### New tests
- `test_export_rejects_partial_book` — Book with 1/5 chapters generated → 400 error
- `test_export_rejects_unapproved_book` — Book with all chapters generated but not approved (when approval required) → 400 error
- `test_export_accepts_fully_generated_book` — Book with all chapters generated → 200 success

---

## BUG 3 (P1): Cold voice loading stalls entire API

### Problem
In `src/engines/model_manager.py` around line 123, `engine.load()` is called synchronously inside an async method. This blocks the entire event loop, meaning ALL other API requests (health, settings, everything) hang until the model finishes loading (which can take 10+ seconds for a 2.9GB model).

```python
async def _load_engine_locked(self, *, reload_count: int) -> None:
    engine = self._engine_factory()
    if not getattr(engine, "loaded", False):
        engine.load()  # SYNCHRONOUS — BLOCKS EVENT LOOP
    self._engine = engine
```

### Fix

**3A. Move blocking load to thread pool:**

```python
async def _load_engine_locked(self, *, reload_count: int) -> None:
    logger.info("Loading shared TTS engine (in background thread)...")
    engine = self._engine_factory()
    if not getattr(engine, "loaded", False):
        # Run blocking model load in a thread to avoid stalling the event loop
        await asyncio.to_thread(engine.load)
    self._engine = engine
    logger.info("TTS engine loaded successfully.")
```

Make sure `import asyncio` is at the top of the file.

**3B. Add a non-blocking voice list endpoint fallback:**

In `src/api/voice_lab.py`, if the engine is not yet loaded, return a degraded response instead of waiting:

```python
@router.get("/voices")
async def list_voices():
    try:
        engine = await asyncio.wait_for(get_engine(), timeout=2.0)
    except asyncio.TimeoutError:
        # Engine still loading — return empty list with a loading flag
        return {"voices": [], "loading": True, "message": "TTS engine is loading. Voices will be available shortly."}

    # ... existing voice listing logic
```

### New tests
- `test_voice_list_returns_loading_when_engine_not_ready` — Mock slow engine load, verify endpoint returns `{"loading": true}` within 2s instead of hanging
- `test_health_check_not_blocked_by_engine_load` — While engine is loading, `/api/health` should still respond

---

## BUG 4 (P2): BookDetail page blocks on voice list fetch

### Problem
In `frontend/src/pages/BookDetail.jsx`, lines 313-315, three API calls are awaited sequentially:
```javascript
await fetchGenerationStatus(requestId);
await fetchExportStatus(requestId);
await fetchVoiceOptions(requestId);  // Slow — blocks entire page
```
The page shows "Loading book details..." until ALL three complete (line 326 sets `loading` to false).

### Fix

**4A. Parallelize the non-dependent fetches:**

```javascript
// Fetch book data and generation/export status in parallel
const [genStatus, exportStatus] = await Promise.all([
    fetchGenerationStatus(requestId),
    fetchExportStatus(requestId),
]);
```

**4B. Move voice fetch out of the loading gate:**

Fetch voices AFTER setting loading=false, so the page renders immediately with book/chapter data while voices load in the background:

```javascript
// Book data loaded — render the page
setLoading(false);

// Voice options load in background (non-blocking)
fetchVoiceOptions(requestId);
```

**4C. Handle loading voices state in the voice section only:**

In the BookDetail component, wherever voice options are displayed, show a small spinner or "Loading voices..." text while `loadingVoiceOptions` is true, instead of blocking the entire page.

### New tests
- Frontend test: BookDetail renders book data even when voice API is slow/pending

---

## BUG 5 (P1): Queue API crashes with timezone-aware vs naive datetime comparison

### Problem
In `src/api/queue_routes.py` around line 273:
```python
recent_cutoff = utc_now() - timedelta(days=7)  # timezone-AWARE
completed_at = job.completed_at or job.created_at  # potentially timezone-NAIVE
return completed_at >= recent_cutoff  # CRASH: TypeError
```

`utc_now()` returns `datetime.now(timezone.utc)` (aware), but SQLite doesn't preserve timezone info, so DB datetimes come back naive.

### Fix

**5A. Create a safe comparison helper in `src/database.py`:**

```python
def ensure_aware(dt: datetime) -> datetime:
    """Ensure a datetime is timezone-aware (UTC). If naive, assume UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt
```

**5B. Use it everywhere datetimes from the DB are compared:**

In `queue_routes.py`:
```python
completed_at = ensure_aware(job.completed_at or job.created_at)
return completed_at >= recent_cutoff
```

**5C. Search and fix ALL datetime comparisons in the codebase:**

Grep for `utc_now()` and find every comparison against a DB datetime field. Apply `ensure_aware()` to all DB datetime values before comparison. Key files to check:
- `src/api/queue_routes.py` — multiple places
- `src/pipeline/queue_manager.py` — job timing logic
- `src/pipeline/exporter.py` — export timing
- `src/api/qa_routes.py` — QA metrics timing
- Any other file that compares `utc_now()` with DB fields

### New tests
- `test_queue_list_with_completed_jobs_no_crash` — Create a completed job, call queue list endpoint, verify no 500 error
- `test_ensure_aware_naive_datetime` — Naive datetime → aware UTC
- `test_ensure_aware_already_aware` — Already aware datetime → unchanged

---

## BUG 6 (P3): QA metrics count unparsed books as "pending QA"

### Problem
In `src/api/qa_routes.py` around line 487:
```python
if not book_chapters:
    books_pending_qa += 1
    continue
```
Books with zero chapters (unparsed books) are counted as "pending QA", making the dashboard show 872 books pending QA before any parsing has happened.

### Fix

**6A. Filter out unparsed books:**

```python
books = db.query(Book).filter(
    Book.status.in_([BookStatus.PARSED, BookStatus.GENERATED, BookStatus.EXPORTED])
).all()
```

Or alternatively, skip books with no chapters AND no generation activity:

```python
if not book_chapters:
    # Only count as pending QA if the book has been parsed (should have chapters)
    # Skip entirely unparsed books
    if book.status in [BookStatus.SCANNED, BookStatus.UNPARSED, BookStatus.ERROR]:
        continue
    books_pending_qa += 1
    continue
```

**6B. Add separate counts for clarity:**

The QA metrics response should include:
```python
{
    "totalBooks": total_books,
    "unparsedBooks": unparsed_count,  # NEW — not ready for QA
    "booksPendingQA": books_pending_qa,  # Only parsed+ books
    "booksApproved": books_approved,
    "booksFlagged": books_flagged,
}
```

### New tests
- `test_qa_metrics_excludes_unparsed_books` — Create 5 books (3 unparsed, 2 parsed), verify pending QA count is 2, not 5
- `test_qa_metrics_includes_parsed_books` — Parsed book with chapters shows in pending QA

---

## Validation Checklist

After all fixes:
- [ ] All 152 existing backend tests pass
- [ ] All 28 existing frontend tests pass
- [ ] New tests for all 6 bug fixes pass
- [ ] Single chapter generation does NOT promote book to GENERATED
- [ ] Export rejects partial books with clear error message
- [ ] Voice list endpoint returns within 2 seconds even during cold start
- [ ] Health check responds while engine is loading
- [ ] BookDetail page renders book data immediately (voices load in background)
- [ ] Queue page works after completed jobs exist (no 500 error)
- [ ] QA dashboard shows accurate pending count (excludes unparsed books)

Commit with message: "[PROMPT-19] CRITICAL BUG FIXES — 6 verified production bugs"
