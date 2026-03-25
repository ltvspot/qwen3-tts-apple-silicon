# Alexandria Audiobook Narrator — Production Audit & Improvement Plan

**Date:** March 24, 2026
**Auditor:** Claude (Cowork PM)
**Scope:** Full codebase audit (Prompts 01–11), remaining prompts (12–16), production readiness for 873-title catalog
**Goal:** Every audiobook gets 5-star reviews. Zero defects. Perfect iteration/quality control loop.

---

## PART 1: SHOWSTOPPER BUGS (Fix Before Any Production Run)

### BUG-01: Speed Control is Completely Broken [CRITICAL]

**File:** `src/engines/qwen3_tts.py` lines 407-408
**Impact:** ALL audiobooks play at 1.0x speed regardless of user setting

The `_apply_speed()` method changes the frame rate to apply speed, then immediately resets it back to the original frame rate — completely undoing the speed change:

```python
adjusted = audio._spawn(audio.raw_data, overrides={"frame_rate": int(audio.frame_rate * speed)})
return adjusted.set_frame_rate(audio.frame_rate)  # ← UNDOES THE SPEED CHANGE
```

**Fix:** Use pydub's proper speed change method, or resample correctly:
```python
adjusted = audio._spawn(audio.raw_data, overrides={"frame_rate": int(audio.frame_rate * speed)})
return adjusted.set_frame_rate(adjusted.frame_rate)  # Keep the new frame rate
# Then resample to target sample rate if needed
```

### BUG-02: Audio Normalization Can Cause Clipping [CRITICAL]

**File:** `src/engines/qwen3_tts.py` lines 410-416
**Impact:** Distorted/clipped audio in final output

Normalization applies gain to reach -18 dBFS but never checks if peaks will exceed 0 dBFS:

```python
def _normalize_audio(self, audio):
    target_dbfs = -18.0
    return audio.apply_gain(target_dbfs - audio.dBFS)  # No peak limiter!
```

If audio has peaks at -3 dBFS and average at -25 dBFS, this adds +7 dB of gain, pushing peaks to +4 dBFS = hard clipping = distortion listeners will hear.

**Fix:** Add a peak limiter: normalize to target, then check if max sample exceeds threshold, and reduce gain if so.

### BUG-03: QA Checker Crashes on Corrupted Audio [CRITICAL]

**File:** `src/pipeline/qa_checker.py` lines 204-224
**Impact:** Entire QA pipeline crashes; chapter marked GENERATED but no QA record created

`_load_audio_analysis()` has NO try-except. If `AudioSegment.from_wav()` fails (corrupted file, partial write, wrong format), the entire QA chain crashes. The chapter stays marked as GENERATED, and subsequent runs skip it.

**Fix:** Wrap audio loading in try-except, return FAIL status with error description for unparseable audio.

### BUG-04: Chapters Marked GENERATED Before QA Runs [CRITICAL]

**File:** `src/pipeline/generator.py` line 239
**Impact:** If QA crashes, chapter is permanently marked as successfully generated with no QA record

The database commit happens BEFORE the QA checker runs. If QA fails for any reason, the chapter is stuck in GENERATED status with no quality assessment.

**Fix:** Either run QA before commit, or make QA failure a non-fatal warning that still creates a QA record (with FAIL status).

---

## PART 2: HIGH-PRIORITY AUDIO QUALITY ISSUES

### AQ-01: Sentence Chunking Breaks on Abbreviations and Numbers

**File:** `src/engines/chunker.py` line 16
**Impact:** "Dr. Smith went to 3.14 Baker St." splits into ["Dr", ". Smith went to 3", ".14 Baker St."]

The sentence boundary regex splits on any period, exclamation, or question mark. This breaks abbreviations (Dr., Mr., Mrs., St., etc.), decimal numbers (3.14, $99.99), and URLs.

**Recommended fix:** Use a smarter sentence tokenizer (NLTK's Punkt tokenizer, or a custom regex with negative lookbehind for common abbreviations). At minimum, add a list of common abbreviations that should NOT trigger a split.

### AQ-02: No Validation of Individual Audio Chunks Before Stitching

**File:** `src/pipeline/generator.py` line 220-221
**Impact:** Bad chunks silently concatenated into final audio

After each chunk is generated, it's appended to the list without checking: minimum duration (a 10ms stub instead of expected text), sample rate consistency, clipping/distortion, or silence (engine returned empty audio).

**Recommended fix:** After each chunk, validate: duration > 100ms, sample rate matches config, peak amplitude < 0 dBFS, RMS > -60 dBFS (not silent).

### AQ-03: Waveform Range Not Validated from MLX Model

**File:** `src/engines/qwen3_tts.py` line 421
**Impact:** Audio corruption if model outputs non-standard range

The code clips waveform to [-1.0, 1.0] but doesn't verify the model's actual output range. If the Qwen3-TTS MLX model outputs [0, 1] or [-2, 2], clipping silently corrupts the audio.

**Recommended fix:** Log the actual min/max of model output on first generation. If outside expected range, warn and scale instead of clip.

### AQ-04: Silence Detection Disabled for Short Chapters

**File:** `src/pipeline/qa_checker.py` lines 191-192
**Impact:** Opening/closing credits (often < 5 seconds) never checked for unwanted silence

The silence detection has a 2-second allowance at start and end. For audio shorter than 4 seconds, the "middle" region becomes empty, so silence is never detected.

**Recommended fix:** For short audio (< 5s), use proportional allowances (10% start, 10% end) instead of fixed milliseconds.

### AQ-05: RMS Calculation Skips Silent Chunks Instead of Flagging Them

**File:** `src/pipeline/qa_checker.py` lines 173-177
**Impact:** Volume consistency check ignores silent sections within a chapter

When a chunk has RMS = 0 (silence), it's skipped instead of being recorded as -inf dB. This means a chapter with 30 seconds of dead silence in the middle passes the volume consistency check.

**Recommended fix:** Record silent chunks as -80 dBFS (noise floor) instead of skipping them. Flag any chunk below -50 dBFS as a potential problem.

### AQ-06: No Crossfade Quality Validation

**File:** `src/pipeline/generator.py` (AudioStitcher)
**Impact:** Audible clicks, pops, or gaps at chunk boundaries

There's no validation that crossfade stitching produces smooth transitions. A bad crossfade (mismatched amplitude, phase cancellation) creates audible artifacts that listeners will notice.

**Recommended fix:** After stitching, run a click/pop detector on chunk boundaries. Flag any amplitude discontinuity > 6 dB as a potential issue.

### AQ-07: No Timeout on MLX Model Generation

**File:** `src/engines/qwen3_tts.py` lines 299-308
**Impact:** Hung generation blocks the entire queue indefinitely

`list(self.model.generate(...))` blocks forever if the model hangs. No timeout, no watchdog.

**Recommended fix:** Wrap generation in a thread with a timeout (e.g., 5 minutes per chunk). If timeout expires, kill and retry.

---

## PART 3: TEXT PARSING & CONTENT ACCURACY

### TP-01: Skip Rules May Miss Variations

**File:** `src/parser/docx_parser.py`
**Impact:** Alexandria marketing content accidentally narrated

The skip rules for "Preface — Message to the Reader" and "Thank You for Reading" use exact string matching. Variations like "PREFACE - Message to the Reader", "Preface: Message to the Reader", or "Thank You For Reading" (capitalization) may slip through.

**Recommended fix:** Use fuzzy matching or normalized comparison (lowercase, strip punctuation, check for key phrases like "message to the reader" and "thank you for reading").

### TP-02: Chapter Detection Relies Solely on Heading 1 Styles

**File:** `src/parser/docx_parser.py`
**Impact:** Books with inconsistent formatting may have wrong chapter boundaries

If a manuscript uses Heading 2 for chapters, or uses bold text without heading styles, the parser won't detect chapters correctly. This silently produces a single giant chapter or misses chapters entirely.

**Recommended fix:** Multi-strategy detection: (1) Heading 1 styles, (2) Heading 2 if no H1 found, (3) pattern matching for "Chapter X" / "CHAPTER X" text, (4) page breaks as fallback. Present detected chapters for human review before generation.

### TP-03: Credits Generator Has Inflexible Format

**File:** `src/parser/credits_generator.py`
**Impact:** Credits sound wrong for books without subtitles or with multiple authors

The format "This is [Title]. [Subtitle]. Written by [Author]. Narrated by Kent Zimering." doesn't handle: no subtitle (says "None" or empty pause), multiple authors, very long titles (awkward pacing).

**Recommended fix:** Conditional formatting: skip subtitle if empty, use "Written by [Author1] and [Author2]" for multiple authors, add a brief pause marker between title and subtitle.

### TP-04: UTF-8 Character Splitting in Chunker

**File:** `src/engines/chunker.py` line 80
**Impact:** Emoji and accented characters corrupted in audio

Character-level splitting doesn't respect grapheme clusters. "Café résumé" could be split mid-accent, producing garbled text for the TTS engine.

**Recommended fix:** Use Python's `grapheme` library or `regex` module with `\X` for grapheme-aware splitting.

### TP-05: Word Count Inconsistency

Three different word count methods are used across the codebase (text_cleaner, docx_parser, database). They may produce different counts for the same text, confusing progress tracking and ETA calculations.

**Recommended fix:** Single canonical `word_count()` function used everywhere.

---

## PART 4: PRODUCTION QUEUE & BULK GENERATION

### PQ-01: No Batch Orchestration for 873-Book Catalog

**Current state:** Queue can process books sequentially, but there's no orchestration layer for managing a full catalog run.

**What's needed for bulk production:**
- Priority tiers: "must ship this week" vs. "backlog"
- Dependency tracking: don't export until all chapters pass QA
- Catalog-level dashboard: X of 873 complete, Y in progress, Z failed
- Estimated time to completion for entire catalog
- Automatic retry with exponential backoff for transient failures
- Resource monitoring: memory usage, disk space, model temperature
- Graceful degradation: if model quality drops after 100+ consecutive generations, pause and cool down

### PQ-02: Can't Cancel Mid-Chapter Generation

**File:** `src/pipeline/queue_manager.py`
**Impact:** If a single chunk takes 30 minutes and user wants to cancel, they must wait

Cancel/pause checks happen between chapters, not within chunk generation. For long chapters with many chunks, this means potentially waiting a very long time.

**Recommended fix:** Pass a cancellation token to the engine's generate() method. Check the token between chunks and abort if set.

### PQ-03: Race Condition on Job Snapshot Updates

**File:** `src/pipeline/queue_manager.py`
**Impact:** Progress/status corruption under concurrent access

The `self.jobs` dict is updated from multiple threads without synchronization. Concurrent reads and writes to job progress, status, and error fields can produce inconsistent state.

**Recommended fix:** Use `threading.Lock` for all `self.jobs` dict access, or switch to a thread-safe data structure.

### PQ-04: Consecutive Failure Limit Too High

**File:** `src/pipeline/queue_manager.py` line 705
**Impact:** 3 consecutive chapter failures before stopping = 3 bad chapters persisted

After 1 chapter fails, the book should be flagged for review. After 2, it should be paused. 3 is too many bad chapters to let through.

**Recommended fix:** Configurable failure threshold (default: 1 for production, 3 for testing). After threshold, pause the job and alert.

### PQ-05: Force Regeneration Doesn't Clean Up Old Files

When force-regenerating a chapter, old audio files are overwritten but not cleaned up if the filename changes. This can leave orphaned audio files consuming disk space.

**Recommended fix:** Delete old audio files before regeneration, or use a staging directory and swap on success.

### PQ-06: No Resource Monitoring

No monitoring of: disk space (873 books × ~50 chapters × ~5MB WAV = ~218 GB before export), memory usage (MLX model + audio processing), CPU temperature (sustained generation can thermal throttle), model quality degradation (TTS models can produce worse output after extended use).

**Recommended fix:** Add resource checks before each generation: disk space > 5 GB free, memory usage < 80%, add optional cool-down period every N chapters.

---

## PART 5: CLAUDE'S PRODUCTION OVERSIGHT PROCESS

This is the most critical section. Here's how I (Claude) should oversee production to guarantee 5-star quality on every audiobook.

### Phase 1: Pre-Production Validation (Per Book)

Before generating any audio, validate:

1. **Manuscript integrity check**
   - DOCX file opens without errors
   - All Heading 1 styles detected and mapped to chapters
   - Skip rules confirmed (no Alexandria preface, no Thank You section)
   - Word count per chapter is reasonable (flag < 100 words or > 50,000 words)
   - No unresolved formatting artifacts (track changes, comments, hidden text)

2. **Metadata verification**
   - Title extracted correctly (compare with folder name)
   - Author name extracted (not empty, not "Unknown")
   - Subtitle present or confirmed absent
   - Chapter count matches table of contents (if present)

3. **Text quality check**
   - No encoding errors (mojibake, replacement characters)
   - Abbreviations in expansion list handled
   - Special characters (em-dashes, ellipses, smart quotes) normalized
   - No excessively long paragraphs (> 2000 chars without period)

4. **Credits preview**
   - Opening credits text generated and reviewed
   - Closing credits text generated and reviewed
   - Narrator name correct ("Kent Zimering")
   - Title/subtitle/author pronunciation reviewed

### Phase 2: Generation with Real-Time QA (Per Chapter)

During generation:

1. **Chunk-level validation**
   - Each chunk duration > 100ms and < 60s
   - Sample rate consistent across all chunks
   - No clipping (peak < -0.5 dBFS)
   - RMS within expected range (-30 to -10 dBFS)
   - No NaN or Inf values in audio data

2. **Stitch-level validation**
   - Crossfade boundaries checked for clicks/pops
   - Total duration within 20% of estimated duration
   - Volume consistent across chunk boundaries (< 3 dB variation)

3. **Chapter-level validation (automated)**
   - Duration within expected range for word count
   - No clipping in final audio
   - No long silences (> 2s) in middle of chapter
   - Consistent volume (< 6 dB variation across chapter)
   - Opening/closing don't start/end abruptly

4. **Chapter-level validation (Claude AI review)**
   - Listen to first 10 seconds and last 10 seconds via API
   - Check that opening words match expected text
   - Check that closing matches expected text
   - Flag any pronunciation that sounds wrong
   - Flag any pacing that sounds unnatural

### Phase 3: Book-Level QA (After All Chapters Generated)

1. **Cross-chapter consistency**
   - Volume normalization consistent across all chapters (-19 LUFS target)
   - Voice consistency (same speaker throughout)
   - Pacing consistency (no chapter suddenly 2x faster)
   - No duplicate chapters (same audio file referenced twice)

2. **Structural integrity**
   - Opening credits present and correct
   - All content chapters present (none skipped)
   - Closing credits present and correct
   - Chapter order matches manuscript
   - Total duration reasonable for book length

3. **Export validation**
   - MP3 file plays correctly (not corrupted)
   - M4B file has correct chapter markers
   - Metadata (title, author, narrator) embedded correctly
   - Cover art embedded (if available)
   - File size reasonable (not 0 bytes, not unexpectedly large)

### Phase 4: Final Approval Gate

Before any audiobook is considered "done":

1. **Automated score card** (all must pass):
   - [ ] No clipping detected
   - [ ] No long silences detected
   - [ ] Volume within -19 LUFS ± 1 LUFS
   - [ ] All chapters present
   - [ ] Credits correct
   - [ ] Duration within expected range
   - [ ] Export files valid

2. **Claude AI review** (sample-based):
   - Listen to opening credits
   - Listen to chapter 1 opening (30s)
   - Listen to a random middle chapter (30s)
   - Listen to closing credits
   - Score: pronunciation, pacing, naturalness, consistency

3. **Tim final approval** (for first 10 books, then spot-check):
   - Full listen of first 3 audiobooks
   - Spot-check of every 10th audiobook after that
   - Any book Tim flags goes back to Phase 2

### Phase 5: Continuous Improvement

After each batch of 50 books:

1. **Quality metrics review**
   - Average QA scores trending up or down?
   - Most common failure modes?
   - Chapters requiring most re-generation?

2. **Model performance monitoring**
   - Is audio quality degrading over time?
   - Are certain text patterns causing issues?
   - Does the model need a restart/cooldown?

3. **Process refinement**
   - Update skip rules based on new edge cases found
   - Update abbreviation list based on failed generations
   - Adjust QA thresholds based on false positive/negative rates

---

## PART 6: QWEN3-TTS SPECIFIC ISSUES & MITIGATIONS

### Model-Specific Risks

1. **Hallucination/repetition**: Qwen3-TTS (like all autoregressive TTS) can get stuck in loops, repeating the same phrase. **Mitigation:** Check that generated audio duration is proportional to input text length. If duration > 3x expected, flag and regenerate.

2. **Silence injection**: Model may insert long pauses mid-sentence. **Mitigation:** Silence detector in QA checker already handles this, but thresholds need tuning for this specific model.

3. **Mispronunciation of proper nouns**: The model doesn't know how to pronounce character names, place names, or domain-specific terms. **Mitigation:** Build a pronunciation dictionary that maps problem words to phonetic spellings. Pre-process text to replace problem words before sending to TTS.

4. **Emotion bleed**: If emotion is set for one chunk, the model may carry that emotion into subsequent chunks. **Mitigation:** Reset emotion state between chunks, or use neutral emotion for all narration (since Kent Zimering's voice should be consistent).

5. **Memory pressure on Apple Silicon**: The 1.7B model uses ~3 GB VRAM. Long generation sessions may cause memory pressure, leading to slower generation or OOM. **Mitigation:** Monitor memory between chapters. If usage > 80%, force garbage collection and optionally reload the model.

6. **Sample rate mismatch**: Qwen3-TTS may output at 24kHz while the pipeline expects 44.1kHz. **Mitigation:** Verify sample rate on first chunk and resample if needed. Log any resampling operations.

7. **Non-deterministic output**: Same text + same voice may produce slightly different audio each time. **Mitigation:** This is expected and acceptable. But if regenerating a single chapter, the voice may sound slightly different from surrounding chapters. Consider regenerating adjacent chapters too for consistency.

### Recommended Qwen3-TTS Configuration

```
Speed: 1.0 (since speed control is broken — fix first)
Voice: Ethan (until Kent Zimering clone is ready)
Emotion: None/Neutral (for consistency)
Chunk size: 200-400 characters (sweet spot for this model)
Sample rate: Match model's native rate, resample at export
```

---

## PART 7: FRONTEND & UX IMPROVEMENTS

### Missing Bulk Operations
- No "select all" and "generate all" from library view
- No batch QA approval (must approve each chapter individually)
- No batch export (must export one book at a time)
- Queue has limited batch controls

### Missing Monitoring
- No catalog-level progress dashboard (X of 873 complete)
- No generation history timeline
- No audio quality trend graphs
- No disk space / memory usage indicators

### UX Polish Needed
- Voice Lab preset saving uses `window.prompt()` — should be a proper modal
- BookDetail has 17 useState() calls — needs state management refactor
- No unsaved changes warning when navigating away from chapter editor
- Export polling interval may leak on unmount (memory leak)
- Queue polling stops after 3 failures with no "resume" button
- No 404 fallback route in App.jsx

### Missing Error Recovery
- Library scan failure: no retry, no partial results shown
- Generation failure: no auto-retry from UI (only manual)
- Export failure: no way to resume or retry
- QA review: optimistic UI update without verifying DB write succeeded

---

## PART 8: HARDENING CHECKLIST FOR PROMPTS 12-16

These items should be addressed in the remaining prompts or as a PROMPT-17 hardening pass:

### For PROMPT-12 (Export Pipeline)
- [ ] Streaming export for very large audiobooks (500+ chapters)
- [ ] Export progress callback to frontend
- [ ] Validate MP3/M4B output integrity after encoding
- [ ] Handle disk space exhaustion gracefully
- [ ] Resume interrupted exports

### For PROMPT-13 (Settings)
- [ ] Settings validation (both client and server side)
- [ ] "Reset to defaults" button
- [ ] Settings migration when schema changes

### For PROMPT-14 (Voice Cloning)
- [ ] Duplicate voice name check before saving
- [ ] Voice preview before committing clone
- [ ] Validate reference audio format and quality
- [ ] Clean up voice files on deletion

### For PROMPT-15 (EPUB/PDF Parsers)
- [ ] Handle multi-column PDF layouts
- [ ] Handle scanned/image-based PDFs (OCR fallback)
- [ ] Progress feedback for slow PDF extraction
- [ ] Graceful fallback chain: DOCX → EPUB → PDF

### For PROMPT-16 (Polish/Hardening)
- [ ] Fix BUG-01 (speed control)
- [ ] Fix BUG-02 (normalization clipping)
- [ ] Fix BUG-03 (QA crash on corrupted audio)
- [ ] Fix BUG-04 (commit before QA)
- [ ] Add resource monitoring (disk, memory)
- [ ] Add generation timeout
- [ ] Add chunk validation before stitching
- [ ] Add pronunciation dictionary support
- [ ] Add bulk QA approval
- [ ] Add catalog progress dashboard
- [ ] Threading locks for queue manager

### NEW: PROMPT-17 (Production Readiness) — Recommended
- [ ] Claude AI review integration (API endpoint for sampling audio)
- [ ] Automated scorecard system
- [ ] Pronunciation dictionary management UI
- [ ] Model cooldown/restart logic
- [ ] Batch progress tracking across entire catalog
- [ ] Quality metrics dashboard
- [ ] Backup/restore for database and generated audio

---

## PART 9: PRIORITY MATRIX

### Must Fix Before First Production Run
| # | Issue | Effort | Impact |
|---|-------|--------|--------|
| BUG-01 | Speed control broken | 1 hour | All audiobooks affected |
| BUG-02 | Normalization clipping | 2 hours | Distorted audio |
| BUG-03 | QA crash on bad audio | 1 hour | Lost QA data |
| BUG-04 | Commit before QA | 2 hours | Unvalidated chapters |
| AQ-01 | Abbreviation chunking | 3 hours | Broken word synthesis |
| AQ-02 | No chunk validation | 3 hours | Bad audio in output |

### Should Fix Before Bulk Production (873 books)
| # | Issue | Effort | Impact |
|---|-------|--------|--------|
| PQ-01 | No batch orchestration | 8 hours | Can't manage catalog |
| PQ-06 | No resource monitoring | 4 hours | OOM/disk full crashes |
| AQ-07 | No generation timeout | 2 hours | Hung queue |
| PQ-03 | Race condition in queue | 3 hours | Status corruption |
| TP-02 | Chapter detection fallback | 4 hours | Wrong chapter boundaries |

### Nice to Have for 5-Star Quality
| # | Issue | Effort | Impact |
|---|-------|--------|--------|
| AQ-06 | Crossfade validation | 4 hours | Audible clicks/pops |
| TP-03 | Credits flexibility | 2 hours | Awkward credit pacing |
| Phase 5 | Quality metrics dashboard | 8 hours | Continuous improvement |
| PROMPT-17 | Full production readiness | 16 hours | Complete oversight |

---

## CONCLUSION

The Alexandria Audiobook Narrator is architecturally sound at ~60% production readiness. The remaining prompts (12-16) will bring it to ~80%. To reach the 100% quality standard needed for 5-star reviews on every audiobook, we need:

1. **Fix the 4 showstopper bugs** (especially speed control — this alone makes every audiobook wrong)
2. **Add chunk-level audio validation** (the single biggest quality gate missing)
3. **Build the production oversight process** (Phases 1-5 above)
4. **Add a PROMPT-17** for production readiness features not covered by existing prompts
5. **Tune QA thresholds** specifically for Qwen3-TTS output characteristics

The production oversight process I've outlined ensures that I (Claude) can systematically verify every audiobook before it's considered complete. The key insight is that quality must be checked at every level — chunk, chapter, book, and catalog — with both automated and AI-assisted review.
