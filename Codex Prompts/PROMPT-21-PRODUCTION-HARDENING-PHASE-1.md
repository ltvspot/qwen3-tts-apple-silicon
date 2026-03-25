# PROMPT-21: Production Hardening Phase 1 — Chunk Validation, Queue Safety, QA Expansion

## Context

The audiobook narrator must produce 5-star quality across 872 books (~43,650 chapters). The core generation pipeline works, but several gaps allow bad audio to slip through undetected, and the queue has race conditions that corrupt state under load.

This prompt addresses the highest-impact production issues. Read CLAUDE.md and PROJECT-STATE.md first.

---

## Task 1: Per-Chunk Audio Validation

**Problem:** After each TTS chunk is generated, it gets appended to the chapter audio WITHOUT validation. Bad chunks (silent, clipping, wrong duration, wrong sample rate) are silently stitched into the final chapter.

**File: `src/pipeline/chunk_validator.py`** (NEW)

Create a chunk validator that runs after every `generate_chunk_with_timeout()` call:

```python
"""Post-generation validation for individual audio chunks."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from pydub import AudioSegment

logger = logging.getLogger(__name__)

@dataclass(slots=True)
class ChunkValidationResult:
    """Result of validating a single audio chunk."""
    valid: bool
    issues: list[str]
    rms_dbfs: float
    peak_dbfs: float
    duration_ms: int
    sample_rate: int

class ChunkValidator:
    """Validate individual audio chunks before they are stitched together."""

    MIN_DURATION_MS = 100          # Chunks shorter than 100ms are likely garbage
    MAX_DURATION_MS = 120_000      # 2 minutes max per chunk
    MIN_RMS_DBFS = -55.0           # Below this = effectively silent
    MAX_PEAK_DBFS = -0.3           # Above this = likely clipping
    EXPECTED_SAMPLE_RATES = {22050, 24000, 44100, 48000}

    @classmethod
    def validate(
        cls,
        audio: AudioSegment,
        expected_sample_rate: int | None = None,
        text_length: int = 0,
    ) -> ChunkValidationResult:
        """Validate a generated audio chunk. Returns result with issues list."""
        issues: list[str] = []
        duration_ms = len(audio)
        rms_dbfs = audio.dBFS if audio.dBFS != float("-inf") else -100.0
        peak_dbfs = audio.max_dBFS if audio.max_dBFS != float("-inf") else -100.0
        sample_rate = audio.frame_rate

        # Duration checks
        if duration_ms < cls.MIN_DURATION_MS:
            issues.append(f"Too short: {duration_ms}ms (min {cls.MIN_DURATION_MS}ms)")
        if duration_ms > cls.MAX_DURATION_MS:
            issues.append(f"Too long: {duration_ms}ms (max {cls.MAX_DURATION_MS}ms)")

        # Silence check
        if rms_dbfs < cls.MIN_RMS_DBFS:
            issues.append(f"Effectively silent: RMS {rms_dbfs:.1f} dBFS (min {cls.MIN_RMS_DBFS})")

        # Clipping check
        if peak_dbfs > cls.MAX_PEAK_DBFS:
            issues.append(f"Clipping risk: peak {peak_dbfs:.1f} dBFS (max {cls.MAX_PEAK_DBFS})")

        # Sample rate check
        if expected_sample_rate and sample_rate != expected_sample_rate:
            issues.append(f"Sample rate mismatch: got {sample_rate}, expected {expected_sample_rate}")

        # Duration vs text length heuristic (very rough: ~150 words/minute, ~5 chars/word)
        if text_length > 20:
            expected_min_ms = (text_length / 5 / 200) * 60 * 1000  # 200 WPM = fast
            expected_max_ms = (text_length / 5 / 80) * 60 * 1000   # 80 WPM = slow
            if duration_ms < expected_min_ms * 0.3:
                issues.append(f"Suspiciously short for {text_length} chars: {duration_ms}ms vs expected {expected_min_ms:.0f}-{expected_max_ms:.0f}ms")
            if duration_ms > expected_max_ms * 3.0:
                issues.append(f"Suspiciously long for {text_length} chars (possible hallucination): {duration_ms}ms vs expected {expected_min_ms:.0f}-{expected_max_ms:.0f}ms")

        return ChunkValidationResult(
            valid=len(issues) == 0,
            issues=issues,
            rms_dbfs=rms_dbfs,
            peak_dbfs=peak_dbfs,
            duration_ms=duration_ms,
            sample_rate=sample_rate,
        )
```

**File: `src/pipeline/generator.py`** — Wire chunk validation into the generation loop.

After `_generate_chunk_with_retry()` returns audio (around line 243), validate the chunk:

```python
from src.pipeline.chunk_validator import ChunkValidator

# After audio = await self._generate_chunk_with_retry(...)
validation = ChunkValidator.validate(
    audio,
    expected_sample_rate=engine.sample_rate if hasattr(engine, 'sample_rate') else None,
    text_length=len(chunk),
)
if not validation.valid:
    logger.warning(
        "Chunk %d validation issues for book %s ch %s: %s",
        chunk_index, book_id, chapter.number, "; ".join(validation.issues),
    )
    # Still append the chunk but flag for manual review
    manual_review_notes.append(
        f"Chunk {chunk_index} validation warnings: {'; '.join(validation.issues)}"
    )
```

**File: `tests/test_chunk_validator.py`** (NEW)

Write tests for:
- Silent audio detection
- Clipping detection
- Duration too short / too long
- Sample rate mismatch
- Duration vs text length heuristic (hallucination detection)
- Valid audio passes all checks

---

## Task 2: Queue Race Condition Fix

**Problem:** `queue_manager.py` uses `self._jobs_lock` inconsistently. The `active_jobs` set and progress fields are accessed without locking in some paths, causing state corruption under concurrent access.

**File: `src/pipeline/queue_manager.py`**

### 2a. Ensure all `active_jobs` access is locked

Find every place `self.active_jobs` is read or written and ensure it's wrapped in `with self._jobs_lock:`. Key locations to audit:
- `_process_job()` — already uses lock at lines 634 and 641 ✓
- Any API-facing methods that read `active_jobs` — ensure they lock
- `_has_pending_jobs()` — should lock when checking job counts

### 2b. Add duplicate job prevention

Before queuing a new job for a book, check if that book already has an active or queued job:

```python
def _has_active_job_for_book(self, book_id: int, db: Session) -> bool:
    """Check if book already has a queued or running job."""
    return db.query(GenerationJob).filter(
        GenerationJob.book_id == book_id,
        GenerationJob.status.in_((GenerationJobStatus.QUEUED, GenerationJobStatus.RUNNING)),
    ).first() is not None
```

Wire this check into the job creation path. If a duplicate is detected, return a 409 Conflict instead of creating a duplicate job.

### 2c. Polling failure recovery

**File: `frontend/src/components/GenerationProgress.jsx`**

The failure counter increments but never resets on success. After 3 failures, polling stops forever. Fix:
- Reset failure counter to 0 on every successful poll
- Use exponential backoff on failures (2s → 4s → 8s) instead of hard stop
- After 10 consecutive failures, show a "Connection lost — click to retry" message instead of silently stopping

**File: `frontend/src/pages/Queue.jsx`**

Same polling fix: reset failure counter on success, exponential backoff on failure.

---

## Task 3: QA Pipeline Expansion

**Problem:** Only 5 automated QA checks exist. Need at least 3 more to catch issues humans would notice.

**File: `src/pipeline/qa_checks.py`** (or wherever QA checks are defined)

### 3a. Add Chunk Boundary Click Detection

After chunks are stitched with crossfade, check for amplitude discontinuities at stitch points. A click/pop sounds like a brief (1-5ms) spike that's 12+ dB louder than surrounding audio.

```python
def check_stitch_clicks(audio: AudioSegment, crossfade_ms: int = 30) -> QACheckResult:
    """Detect clicks at chunk boundary stitch points."""
    # Analyze audio in 5ms windows. Flag if any window's peak is 12+ dB above
    # the average of the surrounding 100ms windows.
    # Return WARNING if 1-2 clicks found, FAIL if 3+ clicks found.
```

### 3b. Add Pacing Consistency Check

Split the chapter audio into 10-second windows. Compute words-per-minute for each window (using text alignment heuristic based on duration proportion). Flag if any window is >40% faster or slower than the chapter average.

```python
def check_pacing_consistency(audio: AudioSegment, text: str) -> QACheckResult:
    """Check for unnaturally fast or slow sections within a chapter."""
    # Split into 10-second windows
    # Estimate WPM per window based on proportional text length
    # Flag if variance exceeds threshold
```

### 3c. Add LUFS Measurement (Post-Export)

After export, measure integrated LUFS of the final file. Audiobook distribution (ACX) requires -18 to -23 LUFS. Flag if outside range.

```python
def check_lufs_compliance(audio_path: str) -> QACheckResult:
    """Verify exported audio meets loudness standard."""
    # Use ffmpeg to measure integrated LUFS:
    # ffmpeg -i file.mp3 -af loudnorm=print_format=json -f null -
    # Parse output JSON for input_i (integrated loudness)
    # PASS if -23 <= lufs <= -18
    # WARNING if -25 <= lufs < -23 or -18 < lufs <= -16
    # FAIL if outside those ranges
```

### 3d. Bulk QA Approval Endpoint

**File: `src/api/qa_routes.py`**

Add a new endpoint to approve all chapters in a book that passed every automated check:

```python
@router.post("/book/{book_id}/approve-all-passing")
async def approve_all_passing_chapters(book_id: int, db: Session = Depends(get_db)):
    """Approve all chapters that passed every automated QA check."""
    # Query chapters where all automated checks passed
    # Set manual_status = "approved", reviewed_by = "auto-approved"
    # Return count of approved chapters
```

**File: `frontend/src/pages/QADashboard.jsx`**

Add an "Approve All Passing" button per book in the QA dashboard that calls this endpoint.

---

## Task 4: Frontend Error Boundaries & Duplicate Prevention

### 4a. Batch Queue Duplicate Prevention

**File: `frontend/src/pages/Queue.jsx`**

The "Generate All Parsed Books" / batch submit button has no disabled state during submission. Users can click multiple times and queue duplicates.

Fix: Add `submitting` state. Disable the button while the POST request is in flight. Show a spinner on the button.

### 4b. CatalogDashboard Promise.all Fix

**File: `frontend/src/pages/CatalogDashboard.jsx`**

`Promise.all()` rejects if ANY single fetch fails, crashing the entire dashboard. Replace with `Promise.allSettled()` and handle partial failures gracefully — show available data and error message for failed sections.

### 4c. Library Search Case-Insensitivity

**File: `frontend/src/pages/Library.jsx`**

Search uses `.includes()` which is case-sensitive. Fix: convert both search term and title/author to `.toLowerCase()` before comparison.

---

## Task 5: Tests

Add tests for ALL new code:

**`tests/test_chunk_validator.py`** — 8+ tests covering every validation rule
**`tests/test_qa_checks_expanded.py`** — Tests for click detection, pacing consistency, LUFS check
**`tests/test_queue_duplicate_prevention.py`** — Test that duplicate jobs are rejected with 409
**`tests/test_bulk_qa_approval.py`** — Test the approve-all-passing endpoint
**Frontend tests** — Test batch button disabled state, Promise.allSettled behavior, case-insensitive search

Run the full test suite. All existing tests must still pass. Report the final count.

---

## Commit Message

```
[PROMPT-21] Production hardening phase 1 — chunk validation, queue safety, QA expansion

- Add ChunkValidator for per-chunk audio validation (duration, silence, clipping, hallucination detection)
- Fix queue race conditions: lock all active_jobs access, add duplicate job prevention
- Fix frontend polling: reset failure counter on success, exponential backoff
- Expand QA pipeline: stitch click detection, pacing consistency, LUFS compliance
- Add bulk QA approval endpoint and frontend button
- Fix CatalogDashboard Promise.all crash, Library case-sensitive search
- Add batch submit duplicate prevention (disabled button during request)
```

## Final Checklist

- [ ] ChunkValidator class created with 6 validation rules
- [ ] Chunk validation wired into generator.py
- [ ] Queue race condition fixed (consistent locking)
- [ ] Duplicate job prevention returns 409
- [ ] Polling failure recovery (reset on success, backoff, retry button)
- [ ] 3 new QA checks (clicks, pacing, LUFS)
- [ ] Bulk QA approval endpoint + frontend button
- [ ] Batch submit button disabled during request
- [ ] CatalogDashboard uses Promise.allSettled
- [ ] Library search is case-insensitive
- [ ] All new code has tests
- [ ] All existing tests still pass
