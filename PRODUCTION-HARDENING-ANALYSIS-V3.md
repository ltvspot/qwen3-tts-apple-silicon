# Production Hardening Analysis V3 — Complete Roadmap to 5-Star Audiobooks

## Assumptions
- PROMPT-21 through PROMPT-26 are implemented
- Three-gate quality pipeline (per-chunk, per-chapter, per-book) is operational
- Progress heartbeats and sentence pause padding are in place
- Model: Qwen3-TTS 1.7B CustomVoice 8-bit quantized, running on Apple Silicon via MLX
- Voice: Ethan / neutral / 1.0x hardcoded as default

---

## PART 1: MODEL-SPECIFIC ISSUES (Qwen3-TTS 1.7B on MLX)

These are known, documented failure modes of this specific model that the app MUST handle.

### 1.1 Infinite Generation Loop (CRITICAL)
**What happens:** The model fails to emit an end-of-sequence token and generates audio indefinitely — producing minutes of garbled, repeating, or silent audio for a 500-character chunk.
**How often:** Rare (~0.5-1% of chunks) but catastrophic when it happens.
**Current mitigation:** 120-second chunk timeout via `asyncio.wait_for()`.
**Gap:** 120 seconds is too long — by the time it triggers, the model has generated 2+ minutes of garbage audio that passes the duration check (it's long, not short). The timeout needs to be **adaptive based on text length**: estimate expected duration (word_count / 2.5 words/sec), then timeout at 3x expected. A 5-second expected chunk should timeout at 15 seconds, not 120.
**Fix needed:** Adaptive timeout + max-audio-duration hard cap (reject any chunk longer than 2x expected duration).

### 1.2 Excessive Silence Insertion (HIGH)
**What happens:** The model inserts 2-27 second silences mid-generation. The 1.7B model is much better than the 0.6B (only 2 pauses >1.5s vs 106), but it still happens.
**Current mitigation:** `check_silence_gaps()` in QA catches gaps >3 seconds, but only at chapter level (after stitching).
**Gap:** A 2.5-second silence mid-sentence passes QA but sounds terrible. And since detection happens after stitching, the bad chunk is already baked into the chapter audio.
**Fix needed:** Per-chunk silence detection (in Gate 1) with a `--max-pause` style trimmer that detects silences >1.5 seconds within a single chunk and trims them to 800ms, preserving 400ms of silence at each edge for smooth transitions.

### 1.3 First-Token Phoneme Bleed (MEDIUM)
**What happens:** In voice cloning (ICL mode), the model's first generated token conditions on whatever phoneme the reference audio ends on. This causes a brief artifact at the very start of generated speech — a half-syllable bleed from the reference.
**Current mitigation:** None.
**Fix needed:** Append 500ms of silence to the reference audio before encoding. Also trim the first 100ms of generated audio if it contains a transient spike inconsistent with the text's first phoneme.

### 1.4 Chinese Accent Bleed (MEDIUM)
**What happens:** Most built-in voice presets have a subtle Chinese accent when speaking English, since the model was trained primarily on Mandarin data. The 1.7B CustomVoice model is better but not immune.
**Current mitigation:** Using voice presets (Ethan, Nova, etc.) which are mapped to specific speakers. But the accent can emerge on unusual English words.
**Fix needed:** Add a "pronunciation watchlist" — a list of English words known to trigger accent artifacts. When these words appear in text, the chunk validator should flag them for extra attention. Also: always use `lang_code="en"` explicitly in generation calls (verify this is happening).

### 1.5 INT8 Quantization Artifacts (LOW)
**What happens:** The 8-bit quantized model occasionally produces subtle tonal artifacts — a slight metallic quality or ringing — compared to the full-precision model. This is the trade-off for 50% less memory usage.
**Current mitigation:** None specific to quantization artifacts.
**Fix needed:** The spectral quality check in Gate 2 (PROMPT-25) should catch the worst cases. For production, consider having a "reference comparison" test: generate the same test passage monthly with the current model and compare spectral characteristics to a known-good baseline.

### 1.6 Memory Pressure Quality Degradation (HIGH)
**What happens:** When the Mac approaches memory limits (>12GB used), MLX starts swapping to disk. Generation doesn't fail — it just gets slower and the audio quality subtly degrades (more artifacts, less natural prosody). The model manager triggers a reload at 12GB threshold, but the degradation starts before that.
**Current mitigation:** Model reload at 12GB memory threshold. Resource monitor pauses queue at 85% memory.
**Gap:** The 12GB threshold is too late — degradation starts around 10GB. Also, the reload clears memory but the 1-second delay isn't enough for MLX to fully release GPU memory.
**Fix needed:** Lower memory threshold to 10GB. Increase post-reload delay to 3 seconds. Add a "generation quality canary" — after every model reload, generate a short test phrase and compare its spectral characteristics to a known-good baseline. If quality drops, reload again.

---

## PART 2: APP ARCHITECTURE ISSUES

### 2.1 No Crash Recovery (CRITICAL)
**What happens:** If the server process dies mid-generation (OOM kill, Mac sleep, power loss), all in-progress jobs are stuck in RUNNING state forever. On restart, the queue manager doesn't know these jobs are orphaned.
**Current mitigation:** None.
**Fix needed:** On startup, scan for all jobs in RUNNING state with `updated_at` older than 5 minutes. Transition them to FAILED with message "Server restarted during generation." Allow manual retry from the UI.

### 2.2 Export Memory Explosion (CRITICAL for long books)
**What happens:** The exporter loads ALL chapter audio files into memory simultaneously for concatenation. A 50-chapter audiobook at ~100MB per chapter = 5GB RAM spike. Combined with the TTS model already using 6GB, this triggers OOM on a 16GB Mac.
**Current mitigation:** None.
**Fix needed:** Streaming concatenation — process chapters sequentially, appending to the output file without holding all chapters in memory. Use ffmpeg's `concat` protocol or `pydub`'s progressive export. Max 2 chapters in memory at any time.

### 2.3 No Job Checkpointing (HIGH)
**What happens:** If a 50-chapter book fails on chapter 48, the next retry starts from chapter 1. All 47 successful chapters are regenerated (wasting hours).
**Current mitigation:** The generator skips chapters that already have GENERATED status (unless `force=True`). But if the job was CANCELLED or FAILED, on retry the status is ambiguous.
**Fix needed:** Explicit checkpoint system: after each chapter completes, record `last_completed_chapter` on the job. On retry, resume from `last_completed_chapter + 1`. Never re-generate a chapter that already passed QA.

### 2.4 SQLite Under Concurrent Load (HIGH)
**What happens:** SQLite's write lock means only one write transaction at a time. When the generator is writing chapter progress AND the API is serving status requests AND the QA checker is writing results, writes queue up. Under heavy load, "database is locked" errors appear.
**Current mitigation:** `check_same_thread=False` allows cross-thread access, but doesn't fix contention.
**Fix needed:** For production with batch generation: either (a) switch to PostgreSQL, or (b) implement WAL mode for SQLite (`PRAGMA journal_mode=WAL`) which allows concurrent reads during writes, plus add retry logic with 500ms backoff on "database is locked" errors.

### 2.5 No Graceful Shutdown (MEDIUM)
**What happens:** If the user quits the app mid-generation, the current chunk is abandoned and the chapter is left in an inconsistent state.
**Current mitigation:** `shutdown_generation_runtime()` exists but doesn't wait for the current chunk to finish.
**Fix needed:** On shutdown signal (SIGTERM/SIGINT), set a "draining" flag that lets the current chunk complete, saves progress, transitions the job to PAUSED, then exits cleanly. Max 30-second drain timeout.

### 2.6 ffmpeg Subprocess Hangs (MEDIUM)
**What happens:** During export, ffmpeg is called for loudness normalization and format conversion. If it receives corrupt audio, it can hang indefinitely.
**Current mitigation:** No timeout on ffmpeg subprocess calls.
**Fix needed:** Add 60-second timeout to all ffmpeg subprocess calls. If timeout triggers, log the corrupt file path and skip that chapter with a warning.

---

## PART 3: BULK GENERATION HARDENING (100+ Books)

### 3.1 Failure Threshold Scaling
**Current:** 5 consecutive failures stops the entire batch.
**Problem:** With 100 books × 20 chapters × 10 chunks = 20,000 chunks, a 0.5% failure rate = 100 failures. If 5 happen consecutively (which is statistically likely), the entire batch stops.
**Fix needed:** Switch to percentage-based thresholds: pause batch if failure rate exceeds 5% of total chunks processed. Also add per-book failure isolation — if one book fails, skip it and continue with the next.

### 3.2 Resource Reservation Per Job
**Current:** Global memory/disk thresholds. If disk has 10GB free and 100 books are queued, the system tries to generate all 100.
**Problem:** The first 20 books fill the disk, then everything pauses. No upfront validation.
**Fix needed:** Before starting a batch, estimate total disk needed: `book_count × avg_chapters × avg_chunk_duration × wav_size_per_second`. If estimated total exceeds 80% of free disk, warn the user before starting.

### 3.3 Batch Priority and Ordering
**Current:** Books processed in queue order (creation time).
**Problem:** A 500-chapter book blocks everything behind it for hours. Short books (5 chapters) wait unnecessarily.
**Fix needed:** Add batch scheduling strategies: (a) shortest-first (process smallest books first for quick wins), (b) round-robin (1 chapter from each book in rotation), (c) priority-based (user sets per-book priority). Default to shortest-first for maximum throughput feedback.

### 3.4 Mac Sleep Prevention
**Current:** No handling of macOS sleep.
**Problem:** During a 12-hour batch generation overnight, the Mac will sleep, suspending all async tasks. On wake, timers are off, connections may be stale, and the generation state is unpredictable.
**Fix needed:** Use `caffeinate` process to prevent sleep during active generation. Start `caffeinate -i` when a job begins, kill it when all jobs complete or the queue is empty. This prevents idle sleep while allowing display sleep.

### 3.5 Disk I/O Optimization
**Current:** Each chunk writes a temporary WAV, then the stitcher reads all chunks and writes the final chapter WAV.
**Problem:** For a 100-book batch with 2,000 chapters and 20,000 chunks, that's 40,000+ disk operations. On an SSD this is fine, but on older Macs with limited SSD endurance, this shortens drive life.
**Fix needed:** Keep chunks in memory when possible (if total chapter audio < 500MB). Only write to disk for chapters exceeding the memory threshold. Use a temp directory with automatic cleanup on completion.

---

## PART 4: THE CLAUDE OVERSIGHT LOOP — 100% Perfect Quality Process

This is the process for me (Claude) to oversee production of each audiobook to guarantee 5-star quality.

### 4.1 Pre-Generation Review
Before starting any audiobook generation:
1. **Parse validation**: Verify the manuscript parsed correctly — chapter count matches expected, no truncated chapters, no encoding artifacts in the text
2. **Text quality scan**: Check for OCR artifacts, missing paragraphs, encoding issues (mojibake), inconsistent formatting
3. **Difficulty assessment**: Flag books with: many proper nouns (pronunciation risk), non-English words, poetry/verse (pacing risk), extensive dialogue (voice switching risk), very short chapters (<500 words, silence ratio risk), very long chapters (>50,000 words, memory risk)
4. **Voice suitability check**: Generate a 30-second test passage from the book's most challenging paragraph. Listen for pronunciation issues, pacing problems, or accent artifacts. If issues found, try alternative voice or adjust settings before committing to full generation.

### 4.2 During Generation — Active Monitoring
While the book is generating:
1. **Watch the first 3 chapters closely**: These establish the baseline. If Gate 1 or Gate 2 flags any issues, STOP and diagnose before continuing. It's cheaper to fix the approach on chapter 3 than to regenerate 50 chapters.
2. **Monitor memory and performance**: Track generation RTF (real-time factor). If it drops below 0.5x (generating slower than 2x real-time), memory pressure is building. Trigger a model reload proactively.
3. **Spot-check every 10th chapter**: Download the audio, listen to a 30-second sample from the middle. Check for drift, artifacts, or pacing issues that automated checks might miss.
4. **Track cumulative quality metrics**: Plot Gate 1 pass rate, Gate 2 grades, and warning counts over time. If quality trends downward, pause and investigate.

### 4.3 Post-Generation — Final QA
After all chapters are generated:
1. **Run Gate 3** (book-level checks): Cross-chapter loudness, voice consistency, pacing consistency, ACX compliance
2. **Run the auto-mastering pipeline**: Loudness normalization, edge silence normalization, peak limiting
3. **Generate the book QA report**: Overall grade, per-chapter grades, any flagged issues
4. **Manual review of flagged chapters**: Any chapter graded C or F must be listened to. Determine if it needs full regeneration or can be fixed with targeted chunk regeneration.
5. **A/B comparison**: Compare the first and last chapters side by side. Voice should sound identical. If not, regenerate the outlier chapters.
6. **Export and verify**: Export in target format (M4B for Audible). Verify chapter markers are correct, metadata is complete, file plays without artifacts in a real audio player.

### 4.4 Post-Export — Final Validation
Before declaring the audiobook complete:
1. **Full playback test**: Play the entire audiobook at 1.5x speed (saves time while still catching issues). Note any audible artifacts, clicks, silence gaps, or voice inconsistencies.
2. **Chapter navigation test**: Skip to random chapters, verify they start at the right place with correct chapter markers.
3. **Format compliance**: Run ACX Audio Lab analysis (or equivalent) to verify all technical specs are met.
4. **Metadata verification**: Title, author, narrator, chapter names, cover art all correct.

### 4.5 Continuous Improvement Loop
After every 10 audiobooks produced:
1. **Analyze failure patterns**: Which Gate catches the most issues? Which check fails most often? This tells us where the model struggles.
2. **Tune thresholds**: If Gate 1 WER check flags 30% of chunks as WARNING but they sound fine, the threshold is too tight. If Gate 2 misses pacing issues that humans catch, the threshold is too loose.
3. **Update pronunciation watchlist**: Add any words that consistently produced artifacts.
4. **Benchmark quality**: Re-generate the standard test passage and compare to the original baseline. If quality has drifted, investigate (model update? config change? memory issue?).

---

## PART 5: REMAINING PROMPTS (27-30)

### PROMPT-27: Crash Recovery, Checkpointing & Graceful Shutdown
- Orphaned job detection on startup (RUNNING + stale updated_at → FAILED)
- Per-chapter checkpointing (last_completed_chapter on job record)
- Graceful shutdown with drain timeout
- SQLite WAL mode + retry on "database is locked"
- Mac sleep prevention via caffeinate during generation

### PROMPT-28: Bulk Generation Hardening
- Percentage-based failure thresholds (5% of total, not 5 consecutive)
- Per-book failure isolation (skip failed book, continue batch)
- Pre-batch resource estimation (disk space, memory, ETA)
- Batch scheduling strategies (shortest-first default)
- Streaming export (max 2 chapters in memory)
- ffmpeg subprocess timeouts (60s)

### PROMPT-29: Model-Specific Mitigations
- Adaptive chunk timeout (3x expected duration, not fixed 120s)
- Per-chunk max-pause trimmer (trim silences >1.5s to 800ms)
- First-token phoneme bleed fix (500ms silence append to reference audio)
- Post-reload quality canary (generate test phrase, verify spectral match)
- Memory threshold lowered to 10GB with 3-second post-reload delay
- lang_code="en" enforced on all generation calls
- Pronunciation watchlist system (configurable word list with known issues)

### PROMPT-30: Claude Oversight Dashboard & Reporting
- Pre-generation manuscript validation API endpoint
- Real-time quality metrics streaming (Gate pass rates, warning counts)
- Book QA report generation and storage
- Per-book difficulty assessment (proper noun density, dialogue ratio, etc.)
- Quality trend tracking across books (rolling 10-book average)
- Export verification checklist API
- "Production Overseer" page in frontend: shows all active/completed books with quality grades, flagged issues, and actionable recommendations
