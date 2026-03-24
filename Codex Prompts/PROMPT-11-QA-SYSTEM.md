# PROMPT-11: Automated QA System with Dashboard

**Objective:** Implement an automated quality assurance system that validates generated audio files, stores QA results, and provides a dashboard for review and approval.

**Owner:** Codex
**Reference:** CLAUDE.md, PROJECT-STATE.md, PROMPT-08 (Generation Pipeline)

---

## Scope

### Automated QA Checker

#### File: `src/pipeline/qa_checker.py`

**Main QA Runner Function:**
```python
async def run_qa_checks(book_id: int, chapter_n: int) -> QAResult:
    """
    Run all QA checks on a generated chapter audio file.
    Called automatically after generation completes.

    Returns: QAResult object with all check results and overall status.
    """
    pass
```

**QA Checks (Run in Sequence)**

1. **file_exists Check**
   - **What:** Verify audio file exists and has > 0 bytes
   - **Implementation:**
     ```python
     def check_file_exists(audio_path: str) -> QACheckResult:
         if not Path(audio_path).exists():
             return QACheckResult(
                 name='file_exists',
                 status='fail',
                 message='Audio file does not exist',
                 value=0
             )

         size_bytes = Path(audio_path).stat().st_size
         if size_bytes == 0:
             return QACheckResult(name='file_exists', status='fail',
                                 message='Audio file is empty')

         return QACheckResult(
             name='file_exists',
             status='pass',
             message=f'File exists ({size_bytes} bytes)',
             value=size_bytes
         )
     ```
   - **Pass Criteria:** File exists and > 0 bytes

2. **duration_check Check**
   - **What:** Verify audio duration is within expected range
   - **Calculation:** `expected_duration = word_count * 0.4 seconds/word` (calibration value)
   - **Tolerance:** ±20% (acceptable range: 0.8x to 1.2x expected)
   - **Implementation:**
     ```python
     def check_duration(audio_path: str, word_count: int) -> QACheckResult:
         audio = AudioSegment.from_wav(audio_path)
         actual_duration = len(audio) / 1000.0  # milliseconds → seconds

         expected_duration = word_count * 0.4
         min_duration = expected_duration * 0.8
         max_duration = expected_duration * 1.2

         if min_duration <= actual_duration <= max_duration:
             return QACheckResult(
                 name='duration_check',
                 status='pass',
                 message=f'Duration {actual_duration:.1f}s within expected range {expected_duration:.1f}s (±20%)',
                 value=actual_duration
             )

         return QACheckResult(
             name='duration_check',
             status='warning',
             message=f'Duration {actual_duration:.1f}s outside expected {expected_duration:.1f}s',
             value=actual_duration
         )
     ```
   - **Pass Criteria:** duration within 0.8x to 1.2x expected
   - **Result:** 'pass' if within range, 'warning' if outside

3. **clipping_detection Check**
   - **What:** Detect audio clipping (peak amplitude too close to max)
   - **Threshold:** Peak amplitude < 0.95 (normalized to 1.0)
   - **Implementation:**
     ```python
     def check_clipping(audio_path: str) -> QACheckResult:
         audio = AudioSegment.from_wav(audio_path)

         # Convert to numpy array for analysis
         samples = np.array(audio.get_array_of_samples(), dtype=np.float32)
         normalized = samples / 32768.0  # 16-bit audio normalization

         peak_amplitude = np.max(np.abs(normalized))

         if peak_amplitude < 0.95:
             return QACheckResult(
                 name='clipping_detection',
                 status='pass',
                 message=f'No clipping detected (peak: {peak_amplitude:.3f})',
                 value=peak_amplitude
             )

         return QACheckResult(
             name='clipping_detection',
             status='fail',
             message=f'Clipping detected (peak: {peak_amplitude:.3f}, threshold: 0.95)',
             value=peak_amplitude
         )
     ```
   - **Pass Criteria:** peak amplitude < 0.95

4. **silence_gaps Check**
   - **What:** Detect long silences in the middle of chapters (indicates generation error)
   - **Threshold:** No silence gaps > 3 seconds mid-chapter
   - **Edge Cases:** Opening/closing silence (first 1 second, last 2 seconds) is acceptable
   - **Implementation:**
     ```python
     def check_silence_gaps(audio_path: str) -> QACheckResult:
         audio = AudioSegment.from_wav(audio_path)

         # Use pydub.detect_silence to find quiet sections
         # Silence threshold: -40 dBFS, duration: at least 3 seconds (3000 ms)
         dBFS_threshold = -40
         min_duration_ms = 3000

         silent_segments = pydub.silence.detect_silence(
             audio,
             min_duration_ms=min_duration_ms,
             silence_thresh=dBFS_threshold
         )

         # Filter out silences at start (first 1s) and end (last 2s)
         audio_duration_ms = len(audio)
         start_cutoff = 1000
         end_cutoff = audio_duration_ms - 2000

         mid_chapter_silences = [
             seg for seg in silent_segments
             if start_cutoff < seg[0] < end_cutoff
         ]

         if not mid_chapter_silences:
             return QACheckResult(
                 name='silence_gaps',
                 status='pass',
                 message='No long silence gaps detected',
                 value=0
             )

         max_silence = max(seg[1] - seg[0] for seg in mid_chapter_silences) / 1000.0
         return QACheckResult(
             name='silence_gaps',
             status='warning' if max_silence < 5 else 'fail',
             message=f'Long silence detected: {max_silence:.1f}s',
             value=max_silence
         )
     ```
   - **Pass Criteria:** No mid-chapter silence > 3 seconds
   - **Result:** 'pass' if none, 'warning' if < 5s, 'fail' if > 5s

5. **volume_consistency Check**
   - **What:** Ensure volume doesn't vary wildly (indicates encoding issues)
   - **Threshold:** RMS (Root Mean Square) within 3dB of chapter average
   - **Implementation:**
     ```python
     def check_volume_consistency(audio_path: str) -> QACheckResult:
         audio = AudioSegment.from_wav(audio_path)

         # Calculate RMS for entire chapter
         samples = np.array(audio.get_array_of_samples(), dtype=np.float32)
         rms = np.sqrt(np.mean(samples ** 2))

         # Normalize to dBFS
         if rms > 0:
             rms_dbfs = 20 * np.log10(rms / 32768.0)
         else:
             rms_dbfs = -np.inf

         # Check chunks (1-second windows)
         sample_rate = audio.frame_rate
         chunk_size = sample_rate
         max_chunk_rms = []

         for i in range(0, len(samples), chunk_size):
             chunk = samples[i:i+chunk_size]
             chunk_rms = np.sqrt(np.mean(chunk ** 2))
             if chunk_rms > 0:
                 chunk_rms_dbfs = 20 * np.log10(chunk_rms / 32768.0)
                 max_chunk_rms.append(chunk_rms_dbfs)

         if not max_chunk_rms:
             return QACheckResult(
                 name='volume_consistency',
                 status='pass',
                 message='Audio too short to analyze chunks',
                 value=0
             )

         max_deviation = max(max_chunk_rms) - min(max_chunk_rms)

         if max_deviation <= 3.0:
             return QACheckResult(
                 name='volume_consistency',
                 status='pass',
                 message=f'Volume consistent (max deviation: {max_deviation:.1f}dB)',
                 value=max_deviation
             )

         return QACheckResult(
             name='volume_consistency',
             status='warning',
             message=f'Volume varies significantly ({max_deviation:.1f}dB deviation)',
             value=max_deviation
         )
     ```
   - **Pass Criteria:** RMS variation < 3dB across chunks

**QA Result Data Structures:**
```python
class QACheckResult(BaseModel):
    name: str  # 'file_exists', 'duration_check', etc.
    status: str  # 'pass', 'warning', 'fail'
    message: str  # Human-readable result
    value: float | None  # Numerical value if applicable

class QAResult(BaseModel):
    chapter_n: int
    book_id: int
    timestamp: datetime
    checks: List[QACheckResult]
    overall_status: str  # 'pass', 'warning', 'fail' (worst of all checks)
    notes: str = ""  # Additional notes from manual review

    @property
    def has_warnings(self) -> bool:
        return any(c.status == 'warning' for c in self.checks)

    @property
    def has_failures(self) -> bool:
        return any(c.status == 'fail' for c in self.checks)
```

#### Integration with Generation Pipeline

**File: `src/pipeline/generator.py` (MODIFIED from PROMPT-08)**

After a chapter generation completes:
```python
async def generate_chapter(book_id: int, chapter_n: int, ...):
    # ... existing generation code ...

    # After audio file is saved:
    audio_path = f"outputs/{book_id}/chapters/{chapter_n:02d}-*.wav"

    # Run QA checks
    from src.pipeline.qa_checker import run_qa_checks
    qa_result = await run_qa_checks(book_id, chapter_n)

    # Store QA result in database
    db_qa = QAStatus(
        book_id=book_id,
        chapter_n=chapter_n,
        overall_status=qa_result.overall_status,
        qa_details=qa_result.json(),
        checked_at=datetime.now()
    )
    db.add(db_qa)
    db.commit()

    # If critical failure, log error
    if qa_result.has_failures:
        logger.warning(f"QA FAIL - Book {book_id} Ch {chapter_n}: {qa_result}")
```

### Database Schema

#### New Table: `qa_status`
```sql
CREATE TABLE qa_status (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  book_id INTEGER NOT NULL,
  chapter_n INTEGER NOT NULL,
  overall_status TEXT NOT NULL,  -- 'pass', 'warning', 'fail'
  qa_details TEXT NOT NULL,  -- JSON string of QAResult
  manual_status TEXT,  -- 'approved', 'flagged', null (pending manual review)
  manual_notes TEXT,
  manual_reviewed_by TEXT,  -- 'Claude', 'Tim', etc.
  manual_reviewed_at TIMESTAMP,
  checked_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(book_id, chapter_n),
  FOREIGN KEY (book_id) REFERENCES books(id)
);
```

#### Extend `chapters` table (from PROMPT-01):
```sql
ALTER TABLE chapters ADD COLUMN (
  qa_status_id INTEGER,
  FOREIGN KEY (qa_status_id) REFERENCES qa_status(id)
);
```

### Backend API Endpoints

#### GET /api/book/{id}/chapter/{n}/qa
**Purpose:** Retrieve QA results for a specific chapter

**Response:**
```json
{
  "chapter_n": 1,
  "book_id": 15,
  "overall_status": "warning",
  "automatic_checks": [
    {
      "name": "file_exists",
      "status": "pass",
      "message": "File exists (187654 bytes)",
      "value": 187654
    },
    {
      "name": "duration_check",
      "status": "pass",
      "message": "Duration 47.2s within expected range 50.0s (±20%)",
      "value": 47.2
    },
    {
      "name": "clipping_detection",
      "status": "pass",
      "message": "No clipping detected (peak: 0.87)",
      "value": 0.87
    },
    {
      "name": "silence_gaps",
      "status": "warning",
      "message": "Long silence detected: 4.2s",
      "value": 4.2
    },
    {
      "name": "volume_consistency",
      "status": "pass",
      "message": "Volume consistent (max deviation: 1.8dB)",
      "value": 1.8
    }
  ],
  "checked_at": "2026-03-24T14:30:00Z",
  "manual_status": null,
  "manual_notes": null,
  "manual_reviewed_by": null,
  "manual_reviewed_at": null
}
```

#### POST /api/book/{id}/chapter/{n}/qa
**Purpose:** Submit manual QA review (approval or flagging)

**Request Body:**
```json
{
  "manual_status": "approved",  -- or "flagged"
  "notes": "Audio quality is excellent. Approved for export.",
  "reviewed_by": "Claude"  -- or "Tim"
}
```

**Response:**
```json
{
  "chapter_n": 1,
  "book_id": 15,
  "manual_status": "approved",
  "manual_notes": "Audio quality is excellent. Approved for export.",
  "manual_reviewed_by": "Claude",
  "manual_reviewed_at": "2026-03-24T14:35:00Z"
}
```

#### GET /api/qa/dashboard
**Purpose:** Get QA dashboard data (chapters needing review, stats)

**Query Parameters:**
- `status` (str, optional): 'pass', 'warning', 'fail', 'pending_review'
- `book_id` (int, optional): Filter by book
- `limit` (int, default 50): Max results

**Response:**
```json
{
  "books_needing_review": [
    {
      "book_id": 15,
      "book_title": "The Count of Monte Cristo",
      "book_author": "Alexandre Dumas",
      "chapters_total": 117,
      "chapters_pass": 95,
      "chapters_warning": 15,
      "chapters_fail": 5,
      "chapters_pending_manual": 2,
      "overall_book_status": "warning"
    }
  ],
  "summary": {
    "books_reviewed": 0,
    "chapters_reviewed": 0,
    "chapters_pass": 0,
    "chapters_warning": 0,
    "chapters_fail": 0,
    "chapters_pending_manual": 0
  }
}
```

### Frontend: QA Dashboard Page

#### File: `frontend/src/pages/QADashboard.jsx`

**Layout:**

1. **Header Section**
   - Page title: "QA Dashboard"
   - Summary stats:
     - Total chapters reviewed
     - Chapters passed (green)
     - Chapters with warnings (yellow)
     - Chapters failed (red)
     - Chapters pending manual review (gray)

2. **Filter & Sort Controls**
   - Filter by status: All | Pass | Warning | Fail | Pending Review
   - Filter by book (dropdown)
   - Sort options: Book title | Status | Most recent | Needs attention first

3. **Books Needing Review Section**
   - List of books with QA issues
   - One row per book:
     - Book title (clickable)
     - Book author
     - Status badges:
       - Green box: "95 passed"
       - Yellow box: "15 warnings"
       - Red box: "5 failed"
       - Gray box: "2 pending review"
     - Action button: "Review Chapters"

4. **Chapter Review List**
   - Expandable per-chapter grid showing:
     - Chapter number and title
     - Status badge (color-coded: green=pass, yellow=warning, red=fail, gray=pending)
     - Hover/expand to show check results:
       - file_exists: ✓ or ✗
       - duration_check: ✓ or ✗ or ⚠
       - clipping_detection: ✓ or ✗
       - silence_gaps: ✓ or ✗ or ⚠
       - volume_consistency: ✓ or ✗ or ⚠
     - Quick-play button: "▶ Listen"
     - Approve/Flag buttons (if not already reviewed)
     - Manual review status (if reviewed): "Approved by Claude" or "Flagged by Tim"

#### UI Components (New)

**File: `frontend/src/components/QAStatusBadge.jsx`**
- Display QA status with color and icon
- Props: `status` ('pass' | 'warning' | 'fail' | 'pending')

**File: `frontend/src/components/CheckResultsTable.jsx`**
- Display individual check results (file_exists, duration_check, etc.)
- Props: `checks` (array of QACheckResult)

**File: `frontend/src/components/ChapterQACard.jsx`**
- Single chapter QA result display
- Expandable to show all checks
- Props: `chapter` (chapter data), `qaResult` (QA result), `onApprove`/`onFlag` callbacks

**File: `frontend/src/components/AudioPlayerQuick.jsx`**
- Mini audio player (single button or inline player)
- Props: `audioUrl`, `chapterName`

### Real-Time QA Updates

When a chapter generation completes:
1. Run automated QA checks (in background)
2. Store results in `qa_status` table
3. Frontend polling (from PROMPT-09) detects completion
4. Optionally notify QA Dashboard page via WebSocket or polling

---

## Acceptance Criteria

### Functional Requirements - Automated QA
- [ ] `file_exists` check passes if file > 0 bytes, fails otherwise
- [ ] `duration_check` warns if outside ±20% of expected (word_count * 0.4 sec/word)
- [ ] `clipping_detection` fails if peak amplitude >= 0.95
- [ ] `silence_gaps` warns if > 3s silence mid-chapter, fails if > 5s
- [ ] `volume_consistency` warns if RMS varies > 3dB across 1-second chunks
- [ ] All QA checks run automatically after chapter generation
- [ ] QA results stored in `qa_status` table with JSON details
- [ ] `overall_status` set to worst status among all checks

### Functional Requirements - API
- [ ] `GET /api/book/{id}/chapter/{n}/qa` returns QA results with all check details
- [ ] `POST /api/book/{id}/chapter/{n}/qa` accepts manual review and stores it
- [ ] `GET /api/qa/dashboard` returns books needing review and summary stats
- [ ] Manual review status persists across API calls
- [ ] `manual_reviewed_by` field captures who approved/flagged

### Functional Requirements - UI
- [ ] QA Dashboard page displays all books and their QA status
- [ ] Status badges color-coded (green/yellow/red/gray)
- [ ] Filter by status works correctly
- [ ] Filter by book works correctly
- [ ] Chapter list expandable to show individual check results
- [ ] Quick-play button loads audio player
- [ ] Approve button marks chapter as approved and updates UI
- [ ] Flag button marks chapter as flagged with optional notes
- [ ] Manual review status displayed on chapter rows

### Code Quality
- [ ] All QA checks use numpy and pydub correctly
- [ ] Audio analysis accurate (RMS, peak, silence detection)
- [ ] No hardcoded magic numbers (use constants for thresholds)
- [ ] Proper error handling if audio file corrupted
- [ ] Logging of all QA check results
- [ ] Database transactions for QA result storage
- [ ] Frontend components use functional syntax with hooks
- [ ] No memory leaks in polling or audio playback

### Testing Requirements

1. **QA Check Unit Tests (pytest):**
   - [ ] `test_check_file_exists_pass`: File exists and > 0 bytes
   - [ ] `test_check_file_exists_fail`: File missing or empty
   - [ ] `test_check_duration_pass`: Duration within ±20%
   - [ ] `test_check_duration_warning`: Duration outside range (but not huge)
   - [ ] `test_check_clipping_pass`: Peak < 0.95
   - [ ] `test_check_clipping_fail`: Peak >= 0.95
   - [ ] `test_check_silence_gaps_pass`: No long silences
   - [ ] `test_check_silence_gaps_warning`: Silence 3-5 seconds
   - [ ] `test_check_silence_gaps_fail`: Silence > 5 seconds
   - [ ] `test_check_volume_consistency_pass`: Deviation < 3dB
   - [ ] `test_check_volume_consistency_warning`: Deviation > 3dB

2. **QA Integration Tests:**
   - [ ] Generate chapter → run all QA checks → verify results stored
   - [ ] QA checks handle corrupted audio gracefully
   - [ ] QA results retrievable via API

3. **API Tests:**
   - [ ] `GET /api/book/{id}/chapter/{n}/qa` returns correct shape
   - [ ] `POST /api/book/{id}/chapter/{n}/qa` saves manual review
   - [ ] `GET /api/qa/dashboard` returns all books with issues

4. **Frontend Component Tests:**
   - [ ] QA Dashboard renders without errors
   - [ ] Status badges display with correct colors
   - [ ] Filter by status works
   - [ ] Chapter list expandable
   - [ ] Approve button updates UI and calls API
   - [ ] Flag button opens modal with notes field

5. **Manual Testing Scenario:**
   - [ ] Generate a chapter with normal quality → verify "pass" status
   - [ ] Generate a chapter with silence issue (force) → verify "warning" status
   - [ ] Open QA Dashboard → see chapter with warning
   - [ ] Click "Flag" on chapter → add note → verify saved
   - [ ] Click "Approve" on another chapter → verify status updated
   - [ ] Filter by "Pending Review" → see only unapproved chapters
   - [ ] Refresh page → verify manual review status persists

---

## File Structure

```
src/
  pipeline/
    qa_checker.py                 # NEW: All QA check logic
  models/
    qa_status.py                  # NEW: SQLAlchemy model for qa_status
  api/
    qa_routes.py                  # NEW: QA API endpoints

frontend/src/
  pages/
    QADashboard.jsx               # NEW: QA dashboard page
  components/
    QAStatusBadge.jsx             # NEW: Status badge component
    CheckResultsTable.jsx         # NEW: Check results display
    ChapterQACard.jsx             # NEW: Chapter QA card component
    AudioPlayerQuick.jsx          # NEW: Mini audio player

tests/
  test_qa_checker.py              # NEW: QA check unit tests
  test_qa_api.py                  # NEW: QA API endpoint tests
  test_qa_dashboard.py            # NEW: QA Dashboard component tests
```

---

## Implementation Notes

### QA Check Constants
```python
# src/pipeline/qa_checker.py
QA_THRESHOLDS = {
    'duration_tolerance_percent': 20,  # ±20%
    'words_per_second': 0.4,  # calibration: 1 word = 0.4 seconds
    'clipping_threshold': 0.95,
    'silence_threshold_dbfs': -40,
    'silence_min_duration_ms': 3000,
    'silence_max_acceptable_seconds': 5.0,
    'volume_deviation_threshold_db': 3.0,
    'chunk_duration_seconds': 1
}
```

### Audio Analysis Best Practices
- Always normalize audio to 16-bit scale (-32768 to 32767)
- Use RMS for volume analysis (more perceptually accurate than peak)
- For silence detection, use pydub's `detect_silence` with appropriate thresholds
- Cache analysis results to avoid re-analyzing same file

### Manual Review Workflow
1. QA Dashboard shows chapters needing review (automatic status == warning/fail)
2. User clicks "Review" → see check results
3. User clicks "Approve" (audio is fine despite warning) or "Flag" (needs regeneration)
4. Manual status stored in `qa_status.manual_status`
5. Export pipeline (PROMPT-12) only includes approved chapters

---

## References

- CLAUDE.md § Critical Business Rules
- PROMPT-08: Generation Pipeline (integration point)
- PROMPT-09: Generation UI (progress display)
- pydub: https://github.com/jiaaro/pydub
- numpy: https://numpy.org/
- SQLAlchemy: https://docs.sqlalchemy.org/

---

## Commit Message

```
[PROMPT-11] Implement automated QA system with dashboard

- Add five automated QA checks: file existence, duration, clipping, silence, volume
- Store QA results in qa_status table with detailed JSON
- Implement manual review workflow (approve/flag)
- Create QA Dashboard page with status visualization
- Add QA API endpoints (GET QA results, POST manual review)
- Comprehensive unit and integration tests for all QA checks
```

---

**Status:** READY FOR IMPLEMENTATION
**Estimated Effort:** 10-12 hours (includes audio analysis, API, UI, and testing)
**Dependencies:** PROMPT-01 (schema), PROMPT-08 (generation pipeline)
