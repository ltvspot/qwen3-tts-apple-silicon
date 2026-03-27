# PROMPT-39: Critical Bug Fixes + Production Hardening

**Priority:** CRITICAL
**Scope:** Backend (engines, pipeline, parser)
**Branch:** `master`
**Estimated effort:** 6 tasks
**Source:** COMPREHENSIVE-AUDIT-V2.md findings — everything NOT yet addressed by PROMPT-31 through PROMPT-38

---

## Context

Two comprehensive audits (PRODUCTION-AUDIT.md, COMPREHENSIVE-AUDIT-V2.md) identified ~25 issues. PROMPT-31–38 addressed some, but the following critical bugs and production gaps remain unfixed. This prompt addresses the highest-impact items that block reliable at-scale audiobook production.

---

## Task 1: Fix Speed Control Bug (BUG-01) [CRITICAL]

**File:** `src/engines/qwen3_tts.py` — `_apply_speed()` method (~line 402-408)

The speed control is completely broken. Line 408 resets the frame rate back to original, undoing the speed change:

```python
adjusted = audio._spawn(audio.raw_data, overrides={"frame_rate": int(audio.frame_rate * speed)})
return adjusted.set_frame_rate(audio.frame_rate)  # ← UNDOES THE SPEED CHANGE
```

### Fix:
Replace with proper speed implementation using pydub's frame rate resampling:
```python
def _apply_speed(self, audio: AudioSegment, speed: float) -> AudioSegment:
    if abs(speed - 1.0) < 0.01:
        return audio
    new_frame_rate = int(audio.frame_rate * speed)
    adjusted = audio._spawn(audio.raw_data, overrides={"frame_rate": new_frame_rate})
    # Resample back to original frame rate so playback device works correctly
    # but the audio content is now faster/slower
    return adjusted.set_frame_rate(audio.frame_rate)
```

Wait — the above is the SAME broken code. The correct approach for speed change via frame rate manipulation is:
1. Change frame rate to `original * speed` (makes audio faster/slower)
2. Set frame rate back to original (preserves the speed change but makes it playable at standard rate)
3. This IS the correct approach — the problem might be elsewhere

Actually, investigate more carefully:
- Generate a test with speed=1.5 and speed=0.8
- Verify the output audio duration changes proportionally (1.5x should be ~67% of original duration)
- If the frame rate approach doesn't work with pydub/ffmpeg, use ffmpeg directly: `ffmpeg -i input.wav -filter:a "atempo=1.5" output.wav`
- OR use scipy/numpy resampling for proper time-stretching

### Tests:
- Generate 10-second audio at speed 1.0, 0.8, 1.2, 1.5
- Verify output duration: 10s, 12.5s, 8.33s, 6.67s respectively (±0.5s tolerance)
- Verify audio quality is not degraded (no pitch shift artifacts)
- If pydub frame rate approach is fundamentally broken, implement ffmpeg atempo filter

---

## Task 2: Fix Audio Normalization Clipping (BUG-02) [CRITICAL]

**File:** `src/engines/qwen3_tts.py` — `_normalize_audio()` method (~line 410-416)

Normalization applies gain to reach -18 dBFS but never checks if peaks will exceed 0 dBFS, causing hard clipping and audible distortion.

### Fix:
```python
def _normalize_audio(self, audio: AudioSegment) -> AudioSegment:
    if audio.dBFS == float("-inf"):
        return audio
    target_dbfs = -18.0
    normalized = audio.apply_gain(target_dbfs - audio.dBFS)
    # Peak limiter: prevent clipping
    peak = normalized.max_dBFS
    if peak > -0.5:
        normalized = normalized.apply_gain(-0.5 - peak)
    return normalized
```

### Tests:
- Test with audio that has peaks at -3 dBFS and average at -25 dBFS (high dynamic range)
- Verify normalization brings average close to -18 dBFS
- Verify peaks never exceed -0.5 dBFS after normalization
- Test with already-normalized audio (near -18 dBFS) — should be no-op
- Test with silent audio (dBFS = -inf) — should return unchanged

---

## Task 3: Fix Chapter Status Race Condition (BUG-04)

**File:** `src/pipeline/generator.py` (~lines 231-251)

Chapter status is committed to GENERATED before QA runs. If QA crashes, chapter is permanently stuck in GENERATED without a QA record.

### Fix:
- Move the DB commit to AFTER QA completes successfully
- If QA fails, set chapter status to `GENERATED_NO_QA` (new status) so it's clear QA didn't run
- Always create a QA record — even if QA crashes, create one with status `ERROR` and the exception message
- Add a recovery endpoint: GET `/api/books/{book_id}/chapters/missing-qa` that finds chapters in GENERATED state without a QA record

### Tests:
- Simulate QA crash → verify chapter is not marked GENERATED
- Verify QA record is always created (even on crash)
- Test recovery endpoint returns correct chapters

---

## Task 4: Sentence Chunker Improvements (AQ-01, TP-01, TP-04)

**File:** `src/engines/chunker.py`

Three related issues in the text chunker:

### 4a: Abbreviation handling (AQ-01)
The sentence boundary regex splits on any period. "Dr. Smith went to 3.14 Baker St." becomes garbage.

**Fix:** Add an abbreviation exception list:
```python
ABBREVIATIONS = {"dr", "mr", "mrs", "ms", "prof", "sr", "jr", "st", "ave", "blvd",
                 "etc", "vs", "vol", "rev", "gen", "sgt", "cpl", "pvt", "lt", "col",
                 "fig", "approx", "dept", "est", "govt", "inc", "ltd", "no", "p", "pp"}
```
Before splitting on a period, check if the word before it is in ABBREVIATIONS (case-insensitive).
Also don't split on periods inside decimal numbers (e.g., `3.14`, `$99.99`).

### 4b: Skip rule normalization (TP-01)
Skip rules use exact matching. "PREFACE - Message to the Reader" or "Thank You For Reading" may slip through.

**Fix:** Normalize to lowercase, strip punctuation before comparing against skip patterns.

### 4c: UTF-8 grapheme splitting (TP-04)
"Café résumé" could split mid-accent character.

**Fix:** If the `grapheme` library is available, use it for character-aware splitting. Otherwise, use `unicodedata.normalize('NFC', text)` before splitting.

### Tests:
- "Dr. Smith went to 3.14 Baker St." → single sentence, not split
- "Mr. Jones said hello. She waved back." → two sentences
- "The cost is $99.99. Order now." → two sentences, $99.99 intact
- Skip rules match regardless of case and punctuation
- "Café résumé" survives chunking intact

---

## Task 5: Chunk-Level Audio Validation (MISS-04, AQ-02)

**File:** `src/pipeline/generator.py` (chunk processing loop)

After each TTS chunk is generated, validate before stitching:

### Requirements:
```python
def _validate_chunk(self, chunk: AudioSegment, expected_text: str) -> tuple[bool, str]:
    """Validate a generated audio chunk. Returns (is_valid, reason)."""
    # 1. Minimum duration: chunk for non-empty text should be > 100ms
    if len(chunk) < 100 and len(expected_text.strip()) > 5:
        return False, f"Too short: {len(chunk)}ms for {len(expected_text)} chars"

    # 2. Not silent: RMS should be above noise floor
    if chunk.rms < 10:  # ~-60 dBFS
        return False, f"Silent chunk: RMS={chunk.rms}"

    # 3. No clipping: peaks should be below -0.3 dBFS
    if chunk.max_dBFS > -0.3:
        return False, f"Clipping detected: peak={chunk.max_dBFS:.1f} dBFS"

    # 4. Reasonable duration ratio: ~100-200ms per word expected
    word_count = len(expected_text.split())
    if word_count > 0:
        ms_per_word = len(chunk) / word_count
        if ms_per_word > 2000:  # > 2s per word = likely hallucination/looping
            return False, f"Suspected hallucination: {ms_per_word:.0f}ms/word"
        if ms_per_word < 50:  # < 50ms per word = impossibly fast
            return False, f"Impossibly fast: {ms_per_word:.0f}ms/word"

    return True, "OK"
```

- On validation failure: retry generation up to 3 times
- After 3 failures: skip chunk, add silence placeholder, flag chapter for review
- Log all validation results for debugging

### Tests:
- Valid chunk passes validation
- Silent chunk fails with "Silent chunk" reason
- Clipping chunk fails with "Clipping detected" reason
- 50ms chunk for 20 words fails with "Too short" reason
- Hallucination-duration chunk fails with "Suspected hallucination" reason
- Retry logic works (mock TTS to fail twice then succeed)

---

## Task 6: Generation Timeout (MISS-05, AQ-07)

**File:** `src/engines/qwen3_tts.py` — `generate()` method

`model.generate()` can hang forever if the MLX model enters a loop state. No timeout, no watchdog.

### Requirements:
- Wrap `model.generate()` in a thread with configurable timeout (default: 120 seconds)
- On timeout: cancel/kill the generation thread, log the timeout, return None
- In the generator pipeline: if TTS returns None (timeout), retry up to 2 times with reduced text chunk size
- After all retries fail: skip chunk, log, flag chapter
- Add `TTS_CHUNK_TIMEOUT_SECONDS` to config (default: 120)
- Track timeout count per session — if timeout rate > 10%, trigger model restart (clear `@lru_cache`)

### Implementation approach:
```python
import concurrent.futures

def _generate_with_timeout(self, text: str, timeout: float = 120.0) -> Optional[np.ndarray]:
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(self._raw_generate, text)
        try:
            result = future.result(timeout=timeout)
            return result
        except concurrent.futures.TimeoutError:
            logger.warning(f"TTS generation timed out after {timeout}s for text: {text[:50]}...")
            self._timeout_count += 1
            if self._timeout_count > self._max_timeouts_before_restart:
                self._restart_model()
            return None
```

### Tests:
- Mock slow generation → verify timeout fires at configured time
- Verify retry with smaller chunk on timeout
- Verify model restart triggers after threshold
- Verify normal generation is unaffected by timeout wrapper

---

## Testing Requirements

- All existing tests must continue to pass (366+ tests)
- Add minimum 20 new tests across all 6 tasks
- Run full test suite: `python -m pytest tests/ -x -q`

---

## Files to Modify

| File | Changes |
|------|---------|
| `src/engines/qwen3_tts.py` | Speed control fix, normalization peak limiter, generation timeout |
| `src/pipeline/generator.py` | Chapter status race condition, chunk validation, retry logic |
| `src/engines/chunker.py` | Abbreviation handling, skip rule normalization, UTF-8 safety |
| `src/pipeline/qa_checker.py` | Clipping threshold adjustment (0.95 → 0.98) |
| `tests/` | New tests for all 6 tasks |

---

## Acceptance Criteria

1. Speed control at 1.5x produces audio ~67% the duration of 1.0x
2. Normalized audio peaks never exceed -0.5 dBFS
3. QA crash does not leave chapters in GENERATED state without QA record
4. "Dr. Smith went to 3.14 Baker St." does not split incorrectly
5. Bad chunks are caught, retried, and flagged instead of silently stitched
6. Hung TTS generation is killed after 120s timeout
7. All 386+ tests pass
