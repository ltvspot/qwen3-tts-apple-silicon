# PROMPT-22: Progress Indicators, Heartbeats & Real-Time Feedback Everywhere

## Context
Users currently see vague "Generating..." or "Loading..." text with no indication of progress, ETA, or whether the app has frozen. Every long-running operation needs a visible heartbeat, progress bar, and estimated time remaining.

## RULE: Every operation >2 seconds MUST have:
1. A **progress bar** (determinate if possible, pulsing/animated if indeterminate)
2. A **heartbeat** — a visible elapsed-time counter that ticks every second so users know the app isn't frozen
3. An **ETA** or stage indicator when available
4. A **percentage** when the backend can provide it

---

## Task 1: Voice Preview Generation (VoiceLab.jsx)

**Current:** Button says "Generating preview..." — no progress, no heartbeat.

**Fix:**
- When "Generate Preview" is clicked, show an inline progress card below the button:
  - Pulsing animated progress bar (indeterminate — backend doesn't report chunk progress for previews)
  - Elapsed time counter: "Elapsed: 0:03" ticking every second
  - Stage text: "Synthesizing audio..." → "Processing..." → "Ready"
- The elapsed timer is the **heartbeat** — it proves the app isn't frozen
- On completion, the progress card smoothly transitions to the audio player
- On error, show the error message in the same card with a "Retry" button

**Implementation:**
- Create a new component: `frontend/src/components/ProgressHeartbeat.jsx`
  - Props: `isActive` (bool), `stage` (string), `progressPercent` (number|null for indeterminate), `startTime` (Date)
  - Renders: progress bar (determinate or pulsing), elapsed time counter (auto-increments via setInterval), stage label, optional percentage
  - This component will be **reused everywhere** below
- Wire into VoiceLab.jsx: when `isGenerating` is true, render `<ProgressHeartbeat isActive={isGenerating} stage={generationStage} />`

---

## Task 2: Voice Loading Retry (VoiceLab.jsx)

**Current bug:** When the TTS engine is still loading on startup, `/api/voice-lab/voices` returns `{ loading: true, voices: [] }`. The frontend shows "No voices available" permanently with no retry.

**Fix:**
- In `loadVoices()`, check `payload.loading === true`
- If loading, show "TTS engine is loading... retrying in 3s" message
- Auto-retry every 3 seconds (max 20 retries = 60 seconds)
- Show a small progress indicator: "Engine loading... (attempt 3/20)"
- Once voices arrive, populate normally
- Apply the same fix in `BookDetail.jsx` `fetchVoiceOptions()`

---

## Task 3: Export Progress Bar — Replace Fake Progress (ExportProgressBar.jsx)

**Current:** `ExportProgressBar.jsx` has a HARDCODED `w-2/3` width (always shows 66%). This is fake progress.

**Fix:**
- The export status endpoint (`/api/book/{id}/export/status`) already returns status info
- Add to the backend `ExportJob` model: `progress_percent` field (0-100)
- In the export worker, update progress as each format completes:
  - If exporting 3 formats (mp3, m4b, opus): each format = 33%
  - Within each format: stitching chapters = 0-80%, encoding = 80-100%
- Update `ExportProgressBar.jsx`:
  - Use actual `progress_percent` from polling response
  - Show: `"Exporting... 45% — Encoding MP3 (chapter 3/10)"`
  - Show elapsed time heartbeat
  - Reuse `<ProgressHeartbeat>` component

**Backend changes needed in `src/pipeline/exporter.py`:**
- Track `progress_percent` on the export job
- Update it during stitching and encoding phases
- Return it via the export status endpoint

---

## Task 4: Library Scan Progress (Library.jsx)

**Current:** "Scanning..." button text with no feedback. A scan of 800+ books could take minutes.

**Fix:**
- Add backend endpoint: `GET /api/library/scan/progress` returning:
  ```json
  { "scanning": true, "files_found": 342, "files_processed": 210, "elapsed_seconds": 12 }
  ```
- In `Library.jsx`, when scan is triggered:
  - Show a modal or inline card with `<ProgressHeartbeat>`
  - Poll `/api/library/scan/progress` every 2 seconds
  - Display: "Scanning library... 210 / 342 files (62%) — 0:12 elapsed"
  - On completion: "Scan complete! Found 15 new books." then auto-refresh library

**Backend changes in `src/api/library_routes.py` (or equivalent):**
- Store scan progress in a module-level dict or the database
- Update counts as files are processed
- Add the progress endpoint

---

## Task 5: Voice Clone Progress (VoiceCloneForm.jsx)

**Current:** "Submitting..." text only. Voice cloning involves file upload + audio processing, which can take 30-120 seconds.

**Fix:**
- Split into two visible stages:
  1. **Upload stage:** Use `XMLHttpRequest` with `onprogress` to show upload percentage
  2. **Processing stage:** After upload completes, show "Processing voice sample..." with elapsed heartbeat
- Show: "Uploading reference audio... 75%" → "Processing voice clone... 0:08 elapsed"
- Reuse `<ProgressHeartbeat>` with `stage` prop switching between "Uploading..." and "Processing..."

---

## Task 6: Library Load Progress (Library.jsx)

**Current:** "Loading library..." for potentially large catalogs (800+ books loaded in paginated batches).

**Fix:**
- During the paginated fetch loop, track: `{ loaded: 250, total: 872 }`
- Show inline: "Loading library... 250 / 872 books"
- Use `<ProgressHeartbeat>` with determinate progress bar

---

## Task 7: Queue Batch Submit Progress (Queue.jsx)

**Current:** "Queueing..." spinner when batch-queuing all parsed books.

**Fix:**
- The batch endpoint should return how many jobs were queued
- Show: "Queuing books... 45 / 120 submitted" with progress bar
- If the endpoint is a single POST that returns immediately, at minimum show elapsed heartbeat while waiting

---

## Task 8: Catalog Dashboard Multi-Endpoint Load (CatalogDashboard.jsx)

**Current:** "Loading catalog dashboard..." while 6 API calls run in parallel.

**Fix:**
- Track which endpoints have completed: `[✓ Batch progress, ✓ Resources, ○ QA summary, ○ Activity...]`
- Show a checklist-style loader:
  ```
  Loading dashboard... (4/6)
  ✓ Batch progress
  ✓ Export progress
  ✓ Resources
  ✓ Model stats
  ○ QA summary...
  ○ Recent activity...
  ```
- Each item gets a checkmark as its fetch resolves
- The progress bar fills as endpoints complete (each = ~17%)

---

## Task 9: Chapter Generation — Per-Chunk Heartbeat (GenerationProgress component)

**Current:** GenerationProgress already shows chapter count and ETA (good!), but within a single chapter there's no sub-progress.

**Fix:**
- Add to the generation status response: `current_chunk` and `total_chunks` for the active chapter
- In the backend `generator.py`, update the book/chapter record with chunk progress as each chunk completes
- Show in GenerationProgress: "Chapter 3 of 10 — Chunk 5/12 (42%)" with the elapsed heartbeat
- This gives users granular feedback even during long chapters

**Backend changes in `src/pipeline/generator.py`:**
- After each chunk generates, update a `current_chunk` / `total_chunks` field on the chapter record
- Return these fields via `/api/book/{id}/status`

---

## Task 10: Global ProgressHeartbeat Component Spec

Create `frontend/src/components/ProgressHeartbeat.jsx`:

```jsx
// Props:
// - isActive: boolean — controls visibility and timer
// - stage: string — e.g. "Synthesizing audio...", "Processing..."
// - progressPercent: number | null — null = indeterminate (pulsing bar)
// - showElapsed: boolean (default true) — show "Elapsed: 0:05"
// - showETA: string | null — e.g. "~2:30 remaining"
// - size: "sm" | "md" | "lg" (default "md")

// Features:
// - Elapsed counter auto-increments every second via useEffect/setInterval
// - Progress bar: if progressPercent is number, show determinate bar; if null, show animated pulsing bar
// - Stage text displayed above or beside the bar
// - Smooth transitions on mount/unmount
// - Accessible: role="progressbar", aria-valuenow, aria-valuemin, aria-valuemax
// - Tailwind-only styling matching the existing app design
```

---

## Testing Requirements

1. **Unit tests** for `ProgressHeartbeat` component:
   - Renders elapsed time correctly
   - Updates every second when active
   - Shows determinate vs indeterminate bar
   - Stops timer when isActive becomes false

2. **Integration tests:**
   - VoiceLab: generate preview shows heartbeat, hides on completion
   - VoiceLab: voice loading retry works when engine returns `loading: true`
   - Library: scan shows file count progress
   - Export: real progress percentage replaces fake bar

3. All existing tests must still pass.

4. **Rebuild the frontend** after all changes: `cd frontend && npm run build`

---

## Priority Order
Implement in this order (most impactful first):
1. Task 10 (ProgressHeartbeat component — everything else depends on it)
2. Task 1 (Voice preview — most visible to users testing right now)
3. Task 2 (Voice loading retry — fixes a real bug)
4. Task 3 (Export fake progress — embarrassing placeholder)
5. Task 9 (Per-chunk heartbeat — important for long chapters)
6. Tasks 4-8 (remaining progress indicators)

Run `cd frontend && npm run build` after all frontend changes so the served app is up to date.
