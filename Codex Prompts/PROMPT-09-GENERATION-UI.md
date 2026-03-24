# PROMPT-09: Generation UI with Real-Time Progress & Audio Player

**Objective:** Add generation controls and real-time progress to the Book Detail page. Implement a bottom panel with an inline audio player for completed chapters.

**Owner:** Codex
**Reference:** CLAUDE.md, PROJECT-STATE.md, PROMPT-08 (Generation Pipeline)

---

## Scope

### Frontend Components

#### 1. Generation Controls (Book Detail Page Header)
**File:** `frontend/src/pages/BookDetail.jsx` (add to existing page)

Add two buttons to the book header:
- **"Generate This Chapter"** button (per chapter row in chapter list)
  - Trigger `POST /api/book/{id}/chapter/{n}/generate`
  - Disable button during generation
  - Show loading spinner while generating

- **"Generate All"** button (in header, applies to entire book)
  - Trigger `POST /api/book/{id}/generate-all`
  - Disable all generation buttons while running
  - Show warning: "This will generate all remaining chapters. Continue?"

- **"Re-generate"** button (only on completed chapters)
  - Trigger `POST /api/book/{id}/chapter/{n}/generate` with `force=true`
  - Allow overwriting existing audio files

#### 2. Real-Time Progress Panel
**File:** `frontend/src/components/GenerationProgress.jsx`

New component that appears when generation is active. Features:
- **Overall progress bar** (chapters completed / total chapters)
- **Current chapter info**
  - Chapter name
  - Status text: "Generating... 2.3s of 47s complete"
  - Elapsed time and ETA
- **Chapter list with status icons**
  - ✓ (green) = completed
  - ⏳ (blue) = generating
  - ⏱ (gray) = pending
  - ⚠ (orange) = error (with error message on hover)

**Implementation:**
- Poll `GET /api/book/{id}/status` every 2 seconds during generation
- Response shape:
  ```json
  {
    "book_id": 1,
    "status": "generating",
    "chapters": [
      {
        "chapter_n": 0,
        "status": "completed",
        "generated_at": "2026-03-24T14:30:00Z",
        "audio_duration_seconds": 23.45
      },
      {
        "chapter_n": 1,
        "status": "generating",
        "progress_seconds": 15.3,
        "expected_total_seconds": 47.2,
        "started_at": "2026-03-24T14:32:00Z"
      },
      {
        "chapter_n": 2,
        "status": "pending"
      }
    ],
    "eta_seconds": 120
  }
  ```
- Clear polling interval when generation completes
- Handle network errors gracefully (retry after 3 failures)

#### 3. Bottom Audio Player Panel
**File:** `frontend/src/components/AudioPlayerPanel.jsx`

New fixed-position panel at the bottom of the page (appears when a chapter completes). Features:
- Use **AudioPlayer** component from PROMPT-07
- Chapter selector dropdown (list of completed chapters)
- **Waveform display** (visual representation of audio)
  - Use a simple waveform library (e.g., wavesurfer.js) or canvas-based visualization
  - Show peak amplitude over time
  - Allow scrubbing (click to seek)
- **Stats after completion:**
  - Generation time (e.g., "Generated in 47.2s")
  - Audio duration (e.g., "4m 12s")
  - Words per second (text word count / audio duration)
  - File size (e.g., "18.2 MB WAV")
- **Close button** to hide panel

**Integration with AudioPlayer:**
- Pass `audioUrl={`/api/book/${id}/chapter/${n}/audio`}`
- Pass `chapter={chapter_name}`
- Use the playback controls from PROMPT-07

### Backend Endpoints

#### GET /api/book/{id}/status
**Purpose:** Real-time generation status for polling

**Response:**
```json
{
  "book_id": 1,
  "status": "generating | idle | error",
  "chapters": [
    {
      "chapter_n": 0,
      "status": "completed | generating | pending | error",
      "generated_at": "ISO timestamp or null",
      "audio_duration_seconds": 45.2,
      "error_message": null
    }
  ],
  "current_chapter_n": 1,
  "eta_seconds": 120,
  "started_at": "ISO timestamp or null"
}
```

**Implementation Notes:**
- Query the database for chapter statuses
- Calculate ETA from average generation speed (chapters/minute) using recent completions
- Store `started_at` for the book when first chapter starts
- Return null for `generated_at` and `audio_duration_seconds` if chapter not completed

#### GET /api/book/{id}/chapter/{n}/status
**Purpose:** Individual chapter status (called every 2 seconds)

**Response:**
```json
{
  "chapter_n": 1,
  "status": "completed | generating | pending | error",
  "progress_seconds": 15.3,
  "expected_total_seconds": 47.2,
  "generated_at": "ISO timestamp or null",
  "audio_duration_seconds": 45.2,
  "error_message": null
}
```

**Implementation Notes:**
- If generating, use MLX status or file watcher to estimate progress
- Expected total = word_count * 0.4 seconds/word (calibration value)
- Return only the actual duration once file is complete

#### Existing Endpoints (Verify Implemented from PROMPT-08)
- `POST /api/book/{id}/chapter/{n}/generate` — Trigger single chapter generation
- `POST /api/book/{id}/generate-all` — Trigger full book generation
- `GET /api/book/{id}/chapter/{n}/audio` — Download chapter audio WAV

### UI/UX Details

#### Chapter List Row Updates
Modify chapter rows in BookDetail.jsx to show:
- Chapter name
- Word count
- **Status badge** (with icon from GenerationProgress)
- **"Generate This Chapter"** button (per chapter)
- **"Re-generate"** button (if completed)

#### Generation Progress Panel Styling
- Use Tailwind CSS
- Appear as overlay or inline section (designer discretion)
- Show real-time progress: "Chapter 2 of 10... Estimated 2m 30s remaining"
- Green/blue/gray color scheme matching status badges

#### Audio Player Panel
- Fixed position at bottom of page (z-index: 40)
- Minimal footprint: ~100px height
- Expandable to show full player controls (z-index: 50)
- Close button collapses panel

### Database Schema Updates

**Extend `chapters` table (from PROMPT-01):**
```sql
ALTER TABLE chapters ADD COLUMN (
  status TEXT DEFAULT 'pending',  -- 'pending', 'generating', 'completed', 'error'
  started_at TIMESTAMP,
  completed_at TIMESTAMP,
  audio_duration_seconds FLOAT,
  error_message TEXT
);
```

**Extend `books` table (from PROMPT-01):**
```sql
ALTER TABLE books ADD COLUMN (
  generation_status TEXT DEFAULT 'idle',  -- 'idle', 'generating', 'error'
  generation_started_at TIMESTAMP,
  generation_eta_seconds INTEGER
);
```

---

## Acceptance Criteria

### Functional Requirements
- [ ] "Generate This Chapter" button triggers single chapter generation via API
- [ ] "Generate All" button shows confirmation dialog and triggers full book generation
- [ ] "Re-generate" button appears only for completed chapters and overwrites audio
- [ ] Real-time progress updates via polling (every 2 seconds)
- [ ] Progress panel shows chapter status icons (✓, ⏳, ⏱, ⚠) that update live
- [ ] Bottom audio player panel appears when chapter completes
- [ ] Audio player plays generated chapter audio
- [ ] Waveform display renders audio visualization
- [ ] Stats show generation time, audio duration, words/second, file size after completion
- [ ] Close button hides the audio player panel
- [ ] Chapter selector in audio player switches between completed chapters
- [ ] ETA calculation accurate to within 20% (based on recent generation speed)
- [ ] All buttons disable during generation (prevent duplicate requests)
- [ ] Network errors during polling handled gracefully (retry up to 3 times)

### Code Quality
- [ ] All React components use functional syntax with hooks
- [ ] Tailwind CSS for all styling (no separate CSS files)
- [ ] Proper error boundaries on async operations
- [ ] Loading states on all buttons during generation
- [ ] PropTypes or TypeScript types on all components
- [ ] API responses validated against schema in frontend
- [ ] No console errors or warnings in browser dev tools
- [ ] Memory cleanup: polling interval cleared on component unmount

### Testing Requirements
1. **Manual UI Testing:**
   - [ ] Click "Generate This Chapter" — verify button disables and shows spinner
   - [ ] Generate a 1-chapter book — verify status updates every 2 seconds
   - [ ] Generate a 10-chapter book — verify progress bar updates accurately
   - [ ] Click "Generate All" — verify confirmation dialog appears
   - [ ] Attempt to click "Generate" again while generating — verify button is disabled
   - [ ] After generation completes, click "Re-generate" — verify audio file is overwritten
   - [ ] Close audio player panel — verify it hides
   - [ ] Switch chapters in audio player dropdown — verify correct chapter plays
   - [ ] Network connectivity test: disable network during generation, verify graceful retry

2. **API Testing (pytest):**
   - [ ] `GET /api/book/{id}/status` returns correct shape when idle
   - [ ] `GET /api/book/{id}/status` returns chapters with correct status values during generation
   - [ ] `GET /api/book/{id}/chapter/{n}/status` returns progress_seconds increasing during generation
   - [ ] `GET /api/book/{id}/chapter/{n}/status` returns actual audio_duration_seconds after completion
   - [ ] ETA calculation accurate (use mock generation speed of 2 chapters/minute)
   - [ ] Error messages returned in status when generation fails

3. **Visual Testing:**
   - [ ] Progress panel layout responsive on mobile/tablet/desktop
   - [ ] Status icons render with correct colors
   - [ ] Waveform displays without crashing for audio files 10s-300s long
   - [ ] Audio player controls functional (play/pause/seek)
   - [ ] Stats display correctly formatted (e.g., "4m 12s", "18.2 MB")

---

## File Structure

```
frontend/src/
  components/
    GenerationProgress.jsx        # NEW: Real-time progress display
    AudioPlayerPanel.jsx          # NEW: Bottom panel with player + waveform
    AudioPlayer.jsx               # EXISTING: from PROMPT-07
  pages/
    BookDetail.jsx                # MODIFIED: Add generation buttons

src/
  api/
    book_routes.py                # MODIFIED: Add GET /api/book/{id}/status
                                  #           Add GET /api/book/{id}/chapter/{n}/status

tests/
  test_generation_api.py          # NEW: API endpoint tests
  test_generation_ui.py           # NEW: Component tests (React Testing Library)
```

---

## Implementation Notes

### Real-Time Progress Strategy
- **Why polling instead of WebSockets?** Simpler for MVP, sufficient for 2-second intervals
- **Fallback:** If polling fails 3 times, show error toast and pause polling
- **Cleanup:** Always clear interval in useEffect cleanup to prevent memory leaks

### ETA Calculation
- Track generation speed: chapters per minute from completed jobs
- Formula: `eta_seconds = (chapters_remaining / avg_chapters_per_minute) * 60`
- Use rolling average of last 5 completed chapters
- Default fallback if no historical data: 0.5 chapters/minute (assume ~2 min per chapter)

### Waveform Implementation Options
1. **wavesurfer.js** (recommended): Lightweight, good browser support, canvas-based
2. **react-waveform-player**: Purpose-built React component
3. **Custom Canvas:** Use Web Audio API to analyze audio and draw waveform

### Audio Duration Calculation (Backend)
In generation pipeline, after audio file is created:
```python
from pydub import AudioSegment
audio = AudioSegment.from_wav(audio_path)
duration_seconds = len(audio) / 1000.0  # pydub duration is in milliseconds
```

---

## References

- CLAUDE.md § Narration Structure (for opening/closing credits timing)
- PROMPT-07: Voice Lab UI (AudioPlayer component contract)
- PROMPT-08: Generation Pipeline (generation endpoint details)
- Web Audio API: https://developer.mozilla.org/en-US/docs/Web/API/Web_Audio_API
- wavesurfer.js: https://wavesurfer.xyz/

---

## Commit Message

```
[PROMPT-09] Add generation UI with real-time progress and audio player

- Add "Generate This Chapter" and "Generate All" buttons to Book Detail
- Implement real-time progress polling (every 2 seconds)
- Add GenerationProgress component with status icons
- Add AudioPlayerPanel with waveform display and stats
- Add GET /api/book/{id}/status and /chapter/{n}/status endpoints
- Extend chapters and books table schema
- Comprehensive tests for API and UI components
```

---

**Status:** READY FOR IMPLEMENTATION
**Estimated Effort:** 8-10 hours (includes UI polish and testing)
**Dependencies:** PROMPT-01 (schema), PROMPT-07 (AudioPlayer), PROMPT-08 (generation endpoints)
