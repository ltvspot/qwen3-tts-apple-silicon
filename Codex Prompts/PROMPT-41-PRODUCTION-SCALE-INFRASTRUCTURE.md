# PROMPT-41: Production Scale Infrastructure

**Priority:** HIGH
**Scope:** Backend (engines, pipeline, queue, API) + Frontend (dashboard, batch operations)
**Branch:** `master`
**Estimated effort:** 6 tasks
**Source:** COMPREHENSIVE-AUDIT-V2.md items MISS-01, MISS-02, MISS-03, MISS-06, PQ-04, PQ-05, plus audio QA findings from March 27 2026 analysis

---

## Context

PROMPT-39 fixes critical bugs. PROMPT-40 adds automated QA. This prompt addresses the infrastructure needed to run the 873-book catalog at scale: model lifecycle management, resource monitoring, pronunciation control, batch orchestration, and batch QA operations.

Key finding from audio QA analysis: opening/closing credits are 2-3 LUFS louder than chapter content (-16 to -17 LUFS vs -18 to -19 LUFS target). This creates a jarring volume jump and must be fixed.

---

## Task 1: Credits Loudness Normalization

**Files:** `src/pipeline/generator.py`, `src/engines/qwen3_tts.py`

### Problem
Opening and closing credits are generated with different effective loudness than chapter content. QA analysis shows credits at -16.0 to -17.0 LUFS while chapters are at -18.0 to -18.9 LUFS. This creates a noticeable volume jump.

### Requirements:
- After generating credits audio (opening-credits.wav, closing-credits.wav), apply the same normalization pipeline as chapter audio
- Target: -18.5 LUFS for all raw chapter/credits WAVs (the export loudnorm will bring to -19)
- Add a post-generation loudness check: if any chapter or credits WAV is more than 1.5 LU from the mean of all chapters, re-normalize it
- Log a warning if credits loudness deviates from chapter mean by more than 1 LU

### Tests:
- Generate credits, verify LUFS within 1 LU of chapter mean
- Test re-normalization triggers when deviation > 1.5 LU
- Verify no clipping after normalization (peak < -0.5 dBFS)

---

## Task 2: Model Cooldown and Restart Logic (MISS-01)

**Files:** `src/engines/qwen3_tts.py`, `src/pipeline/queue_manager.py`

### Problem
The Qwen3-TTS MLX model (1.7B params, ~3GB VRAM) is loaded once via `@lru_cache` and never restarted. After 50+ chapters of continuous generation, GPU memory fragmentation accumulates, potentially degrading audio quality and eventually causing OOM crashes on Apple Silicon.

### Requirements:
- Track chapters generated since last model load: `self._chapters_since_restart`
- After every `MODEL_RESTART_INTERVAL` chapters (default: 50, configurable via env `TTS_MODEL_RESTART_INTERVAL`):
  1. Log: "Model cooldown: restarting after {n} chapters"
  2. Clear the `@lru_cache` on the model loader
  3. Force garbage collection (`gc.collect()`)
  4. On Apple Silicon: call `mlx.core.metal.clear_cache()` if available
  5. Re-load the model on next generation call (lazy reload)
  6. Log memory usage before and after restart
- Add a `/api/system/model-status` endpoint returning:
  - `chapters_since_restart`: int
  - `restart_interval`: int
  - `memory_usage_mb`: float (current process RSS)
  - `model_loaded`: bool
- Queue manager should pause generation during model restart (max 10s)

### Tests:
- Verify counter increments per chapter
- Verify restart triggers at threshold
- Verify model is functional after restart (generate test audio)
- Verify API endpoint returns correct data

---

## Task 3: Resource Monitoring System (MISS-02)

**Files:** New `src/monitoring/resource_monitor.py`, `src/api/routes/system_routes.py`

### Problem
For 873 books × ~50 chapters × ~5MB WAV = ~218 GB of audio. No monitoring of disk space, memory, or throughput degradation.

### Requirements:
- Create `ResourceMonitor` class that tracks:
  - Disk space: available GB on output directory filesystem
  - Process memory: RSS in MB
  - Generation throughput: chapters/hour (rolling 1-hour window)
  - Output directory size: total GB used by generated audio

- Pre-generation gate in queue_manager:
  - Before starting each chapter: check disk space >= 2 GB free
  - Check process memory < 80% of system RAM
  - If either fails: pause queue, log warning, emit WebSocket event

- API endpoints:
  - `GET /api/system/resources` — current resource snapshot
  - `GET /api/system/resources/history` — last 24 hours of snapshots (sampled every 5 min)

- Frontend widget:
  - Add "System Resources" card to the generation dashboard
  - Show: disk free, memory used, throughput rate, total output size
  - Color coding: green (>10GB free), yellow (2-10GB), red (<2GB)

### Tests:
- Test disk check with mocked filesystem
- Test memory check with mocked psutil
- Test queue pauses when resources low
- Test API endpoints return valid data

---

## Task 4: Pronunciation Dictionary (MISS-03)

**Files:** New `src/engines/pronunciation_dictionary.py`, `src/engines/chunker.py`, `src/api/routes/settings_routes.py`

### Problem
Qwen3-TTS has no way to handle unusual pronunciations. Character names, place names, and technical terms will be mispronounced across the 873-book catalog.

### Requirements:
- JSON-based pronunciation dictionary at `data/pronunciation.json`:
  ```json
  {
    "global": {
      "Château": "shah-TOH",
      "naïve": "nah-EEV",
      "résumé": "REH-zoo-may"
    },
    "per_book": {
      "29": {
        "Thoreau": "thuh-ROH"
      }
    }
  }
  ```

- Pre-processing step in chunker:
  1. Before sending text to TTS, look up each word in dictionary
  2. Replace the word with its phonetic respelling
  3. Phonetic respelling uses simple English approximation (not IPA)
  4. Per-book entries override global entries

- API endpoints:
  - `GET /api/pronunciation` — full dictionary
  - `PUT /api/pronunciation/global/{word}` — add/update global entry
  - `PUT /api/pronunciation/book/{book_id}/{word}` — add/update per-book entry
  - `DELETE /api/pronunciation/global/{word}` — remove global entry

- Frontend:
  - "Pronunciation" tab in Settings page
  - Table showing all entries (word → pronunciation, scope: global or book-specific)
  - Add/edit/delete functionality
  - Search/filter by word

- Auto-detection suggestion system:
  - After transcription QA (PROMPT-40), if a word has WER mismatch and appears to be a proper noun (capitalized, not in common dictionary), suggest adding it to the pronunciation dictionary

### Tests:
- Test replacement in chunker with known dictionary entries
- Test per-book override of global entry
- Test API CRUD operations
- Test that pronunciation replacement doesn't break sentence boundaries

---

## Task 5: Batch Generation Orchestration (MISS-06)

**Files:** `src/pipeline/queue_manager.py`, `src/api/routes/batch_routes.py` (new), frontend

### Problem
Must manually queue each book one by one. No catalog-level progress, no estimated completion time, no priority tiers. Producing 873 books requires automation.

### Requirements:
- New `BatchRun` model:
  ```python
  class BatchRun:
      batch_id: str
      status: str  # "running", "paused", "completed", "cancelled"
      book_ids: list[int]
      books_completed: int
      books_failed: int
      books_remaining: int
      started_at: datetime
      estimated_completion: Optional[datetime]
      current_book_id: Optional[int]
      settings: dict  # voice, speed, etc.
  ```

- API endpoints:
  - `POST /api/batch/start` — start batch run with list of book IDs (or "all")
  - `GET /api/batch/{batch_id}` — batch status
  - `POST /api/batch/{batch_id}/pause` — pause batch
  - `POST /api/batch/{batch_id}/resume` — resume batch
  - `POST /api/batch/{batch_id}/cancel` — cancel batch
  - `GET /api/batch/active` — current active batch

- Orchestration logic:
  1. Process books sequentially (one at a time to manage memory)
  2. After each book completes: run automated QA
  3. If QA passes (score >= 80 for all chapters): mark book READY_FOR_EXPORT
  4. If QA fails: mark book NEEDS_REVIEW, continue to next book
  5. After every `MODEL_RESTART_INTERVAL` chapters: trigger model cooldown
  6. Check resources before each book: disk space, memory
  7. Track estimated completion: `remaining_books * avg_time_per_book`

- Frontend:
  - "Batch Production" page accessible from main nav
  - Shows: overall progress bar, current book, books completed/failed/remaining
  - Estimated time remaining
  - Per-book status list with QA scores
  - Start/pause/cancel controls

### Tests:
- Test batch start with 3 books
- Test pause/resume maintains state
- Test QA auto-runs after each book
- Test model cooldown integrates with batch
- Test estimated completion calculation

---

## Task 6: Batch QA Approval and Export (PQ-04, PQ-05)

**Files:** `src/api/routes/qa_routes.py`, `src/api/routes/export_routes.py`, frontend

### Problem
QA dashboard requires approving each chapter individually. For 873 books × ~50 chapters = ~43,650 chapters, this is impractical. Similarly, must export one book at a time.

### Requirements:

**Batch QA Approval:**
- `POST /api/qa/batch-approve` — approve all chapters in a book that pass automated QA (score >= threshold)
  - Body: `{ "book_id": int, "min_score": int (default 80) }`
  - Approves all chapters scoring >= min_score
  - Returns: count approved, count below threshold, count already approved
- `POST /api/qa/batch-approve-all` — approve all chapters across all books meeting threshold
- Frontend: "Approve All Passing" button on book QA page + catalog-level "Approve All Passing Books" button

**Batch Export:**
- `POST /api/export/batch` — queue export for multiple books
  - Body: `{ "book_ids": list[int], "formats": ["mp3", "m4b"] }`
  - Processes exports sequentially (to manage disk/memory)
  - Skips books not fully QA-approved
- `GET /api/export/batch/{batch_id}` — export batch status
- Frontend: "Export All Ready" button that exports all QA-approved books

**Export verification:**
- After each export completes, automatically run QA on the exported MP3
- Verify: LUFS within -19 ±1, peak < -3 dBFS, no clipping, duration matches
- If export QA fails: flag for investigation

### Tests:
- Test batch approve with mix of passing/failing chapters
- Test batch approve respects min_score threshold
- Test batch export queues multiple books
- Test export QA verification catches issues
- Test skipping non-approved books in export batch

---

## Testing Requirements

- All existing tests must continue to pass (expected 386+ after PROMPT-39)
- Add minimum 25 new tests across all 6 tasks
- Run full test suite: `python -m pytest tests/ -x -q`

---

## Files to Create/Modify

| File | Changes |
|------|---------|
| `src/pipeline/generator.py` | Credits loudness normalization |
| `src/engines/qwen3_tts.py` | Model cooldown/restart logic |
| `src/pipeline/queue_manager.py` | Resource gate, batch orchestration integration |
| `src/monitoring/resource_monitor.py` | New — disk/memory/throughput tracking |
| `src/engines/pronunciation_dictionary.py` | New — word→phonetic mapping with per-book overrides |
| `src/engines/chunker.py` | Pronunciation replacement pre-processing |
| `src/api/routes/system_routes.py` | Resource + model status endpoints |
| `src/api/routes/settings_routes.py` | Pronunciation dictionary CRUD |
| `src/api/routes/batch_routes.py` | New — batch generation orchestration |
| `src/api/routes/qa_routes.py` | Batch QA approval |
| `src/api/routes/export_routes.py` | Batch export |
| `data/pronunciation.json` | New — default pronunciation dictionary |
| Frontend pages | Batch dashboard, pronunciation UI, resource widget |
| `tests/` | New tests for all 6 tasks |

---

## Acceptance Criteria

1. Credits and chapters have LUFS within 1 LU of each other
2. Model automatically restarts every 50 chapters with no generation interruption
3. Queue pauses when disk space < 2GB or memory > 80%
4. Pronunciation dictionary replaces words in chunker before TTS
5. Batch run processes 3+ books end-to-end with automatic QA
6. "Approve All Passing" approves chapters meeting score threshold in one click
7. Batch export queues multiple books with automatic export QA verification
8. All 410+ tests pass
