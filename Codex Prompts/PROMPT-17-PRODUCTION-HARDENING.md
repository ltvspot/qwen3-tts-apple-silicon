# PROMPT-17: Production Hardening (Tier 1 Critical Fixes)

**Objective:** Fix critical bugs and add essential safety systems needed before any production audio generation.

**Owner:** Codex
**Reference:** CLAUDE.md, PROJECT-STATE.md, COMPREHENSIVE-AUDIT-V2.md

---

## Scope

### 1. FIX: Speed Control Method (BUG-01)

**File:** `src/engines/qwen3_tts.py` — method `_apply_speed()`

Current code at ~line 402-408:
```python
def _apply_speed(self, audio: AudioSegment, speed: float) -> AudioSegment:
    if speed == 1.0:
        return audio
    adjusted = audio._spawn(audio.raw_data, overrides={"frame_rate": int(audio.frame_rate * speed)})
    return adjusted.set_frame_rate(audio.frame_rate)
```

**Fix:** Extract the new frame rate into a variable and add a clarifying comment:
```python
def _apply_speed(self, audio: AudioSegment, speed: float) -> AudioSegment:
    """Apply a simple speed adjustment by changing playback rate."""
    if speed == 1.0:
        return audio
    new_frame_rate = int(audio.frame_rate * speed)
    adjusted = audio._spawn(audio.raw_data, overrides={"frame_rate": new_frame_rate})
    # Resample back to the original frame rate so downstream tools see
    # a consistent sample rate while the perceived speed has changed.
    return adjusted.set_frame_rate(audio.frame_rate)
```

### 2. FIX: Audio Normalization Peak Limiter (BUG-02)

**File:** `src/engines/qwen3_tts.py` — method `_normalize_audio()`

Current code at ~line 410-416:
```python
def _normalize_audio(self, audio: AudioSegment) -> AudioSegment:
    if audio.dBFS == float("-inf"):
        return audio
    target_dbfs = -18.0
    return audio.apply_gain(target_dbfs - audio.dBFS)
```

**Fix:** Add peak limiter to prevent hard clipping after normalization:
```python
def _normalize_audio(self, audio: AudioSegment) -> AudioSegment:
    """Normalize audio toward a consistent output level with peak limiting."""
    if audio.dBFS == float("-inf"):
        return audio
    target_dbfs = -18.0
    normalized = audio.apply_gain(target_dbfs - audio.dBFS)
    # Peak limiter: if normalization pushed peaks above -0.5 dBFS,
    # reduce gain to prevent hard clipping / digital distortion.
    peak_dbfs = normalized.max_dBFS
    if peak_dbfs > -0.5:
        normalized = normalized.apply_gain(-0.5 - peak_dbfs)
    return normalized
```

### 3. ADD: Chunk-Level Audio Validation Before Stitching

**File:** `src/pipeline/generator.py`

After each chunk is generated and BEFORE it's appended to the audio_chunks list, validate:

```python
def _validate_chunk(self, chunk: AudioSegment, chunk_index: int, expected_text: str) -> None:
    """Validate a generated audio chunk before stitching.

    Raises ValueError if chunk is invalid.
    """
    # 1. Duration check: must be > 100ms (not a stub)
    if len(chunk) < 100:
        raise ValueError(f"Chunk {chunk_index} too short: {len(chunk)}ms (min 100ms)")

    # 2. Duration sanity: must be < 120 seconds for any single chunk
    if len(chunk) > 120_000:
        raise ValueError(f"Chunk {chunk_index} too long: {len(chunk)}ms (max 120s) — possible model loop")

    # 3. Not silent: RMS must be above noise floor
    if chunk.dBFS < -55:
        raise ValueError(f"Chunk {chunk_index} is nearly silent: {chunk.dBFS:.1f} dBFS")

    # 4. No clipping: peak must be below 0 dBFS
    if chunk.max_dBFS > -0.1:
        raise ValueError(f"Chunk {chunk_index} is clipping: peak {chunk.max_dBFS:.1f} dBFS")

    # 5. Duration proportional to text (rough check)
    # Average speaking rate ~150 words/min = 2.5 words/sec
    # Allow 3x tolerance for slow speech or pauses
    word_count = len(expected_text.split())
    expected_min_ms = (word_count / 10.0) * 1000  # Very fast: 10 words/sec
    expected_max_ms = (word_count / 0.5) * 1000    # Very slow: 0.5 words/sec
    if len(chunk) > expected_max_ms and word_count > 3:
        raise ValueError(
            f"Chunk {chunk_index} duration {len(chunk)}ms is disproportionate "
            f"to text length ({word_count} words) — possible model hallucination/loop"
        )
```

**Integration in generate_chapter():**

After each chunk is generated, call `_validate_chunk()`. If validation fails, retry up to 2 times. If all retries fail, log the error and skip the chunk (flag the chapter for manual review).

```python
# In the chunk generation loop:
for i, chunk_text in enumerate(text_chunks):
    chunk_audio = None
    last_error = None

    for attempt in range(3):  # Up to 3 attempts
        try:
            raw_chunk = await self._generate_chunk(chunk_text, voice, emotion, speed)
            self._validate_chunk(raw_chunk, i, chunk_text)
            chunk_audio = raw_chunk
            break
        except ValueError as e:
            last_error = e
            logger.warning("Chunk %d attempt %d failed validation: %s", i, attempt + 1, e)
            await asyncio.sleep(0.5 * (attempt + 1))  # Backoff

    if chunk_audio is None:
        logger.error("Chunk %d failed all 3 attempts: %s — skipping", i, last_error)
        # Flag chapter for manual review
        chapter.qa_notes = f"Chunk {i} failed validation after 3 attempts: {last_error}"
        continue

    audio_chunks.append(chunk_audio)
```

### 4. ADD: Generation Timeout Per Chunk

**File:** `src/engines/qwen3_tts.py`

Wrap the MLX model generation call in a timeout. If generation takes longer than 120 seconds for a single chunk, kill it and raise TimeoutError.

```python
import asyncio
import signal
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

CHUNK_TIMEOUT_SECONDS = 120  # 2 minutes max per chunk

async def generate_chunk_with_timeout(self, text: str, voice: str, **kwargs) -> AudioSegment:
    """Generate a single audio chunk with timeout protection."""
    loop = asyncio.get_event_loop()

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(self._generate_chunk_sync, text, voice, **kwargs),
            timeout=CHUNK_TIMEOUT_SECONDS
        )
        return result
    except asyncio.TimeoutError:
        logger.error("Chunk generation timed out after %ds for text: %s...",
                     CHUNK_TIMEOUT_SECONDS, text[:50])
        raise TimeoutError(f"Generation timed out after {CHUNK_TIMEOUT_SECONDS}s")
```

Add the timeout constant to `src/config.py` under EngineSettings:
```python
chunk_timeout_seconds: int = Field(default=120, ge=10, le=600, description="Max seconds per chunk generation")
```

### 5. FIX: Sentence Chunker Abbreviation Handling

**File:** `src/engines/chunker.py`

The current sentence boundary regex splits on any period, breaking abbreviations like "Dr.", "Mr.", "St.", "etc.", and decimal numbers like "3.14".

**Fix:** Add an abbreviation exception list and improve the split regex:

```python
# Common abbreviations that should NOT trigger sentence splits
ABBREVIATIONS = {
    'mr', 'mrs', 'ms', 'dr', 'prof', 'sr', 'jr', 'st', 'ave', 'blvd',
    'dept', 'est', 'fig', 'gen', 'gov', 'inc', 'ltd', 'corp', 'co',
    'vs', 'etc', 'approx', 'appt', 'apt', 'assn', 'assoc',
    'vol', 'rev', 'sgt', 'cpl', 'pvt', 'capt', 'lt', 'col',
    'no', 'nos', 'op', 'ed', 'trans', 'repr',
}

def _is_abbreviation(self, text_before_period: str) -> bool:
    """Check if the text before a period is a known abbreviation."""
    # Get the last word before the period
    words = text_before_period.strip().split()
    if not words:
        return False
    last_word = words[-1].lower().rstrip('.')

    # Check against known abbreviations
    if last_word in ABBREVIATIONS:
        return True

    # Single letter followed by period (e.g., "J. K. Rowling")
    if len(last_word) == 1 and last_word.isalpha():
        return True

    # Check for decimal numbers (e.g., "3.14")
    if last_word.replace('.', '').replace(',', '').isdigit():
        return True

    return False
```

Update the `split_into_sentences()` method to use this check before splitting on periods.

### 6. ADD: 404 Catch-All Route in Frontend

**File:** `frontend/src/App.jsx`

Add a NotFound component and catch-all route:

```jsx
// Add at the end of Route definitions:
<Route path="*" element={<NotFound />} />
```

**File:** `frontend/src/pages/NotFound.jsx` (NEW)

```jsx
import { Link } from 'react-router-dom';

export default function NotFound() {
  return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] text-center">
      <h1 className="text-6xl font-bold text-gray-300 mb-4">404</h1>
      <p className="text-xl text-gray-600 mb-6">Page not found</p>
      <Link
        to="/"
        className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 transition-colors"
      >
        Back to Library
      </Link>
    </div>
  );
}
```

### 7. FIX: Health Check File Cleanup Permission Error

**File:** `src/health_checks.py`

The `check_output_directory_writable()` function creates a test file but fails to delete it in some environments. Wrap the cleanup in try-except:

```python
def check_output_directory_writable(output_dir: Path) -> HealthCheckItem:
    """Check that the output directory exists and is writable."""
    test_file = output_dir / ".write_test"
    try:
        test_file.write_text("test")
        return HealthCheckItem(name="Output Directory", status="ok", message=str(output_dir))
    except (OSError, PermissionError) as e:
        return HealthCheckItem(name="Output Directory", status="error", message=f"Not writable: {e}")
    finally:
        try:
            test_file.unlink(missing_ok=True)
        except (OSError, PermissionError):
            pass  # Cleanup failure is non-critical
```

---

## Acceptance Criteria

### Bug Fixes
- [ ] `_apply_speed()` has clarifying variable and comment
- [ ] `_normalize_audio()` has peak limiter (max_dBFS check after gain)
- [ ] Health check file cleanup wrapped in try-except

### Chunk Validation
- [ ] `_validate_chunk()` method exists in generator.py
- [ ] Checks: duration min/max, silence, clipping, text-proportional duration
- [ ] Failed chunks retry up to 3 times with backoff
- [ ] Permanently failed chunks are skipped and chapter is flagged

### Generation Timeout
- [ ] Chunk generation wrapped in asyncio.wait_for with configurable timeout
- [ ] Timeout constant in config.py (default: 120s)
- [ ] Timeout raises clear error, logged with chunk text

### Sentence Chunking
- [ ] ABBREVIATIONS set with 30+ common abbreviations
- [ ] `_is_abbreviation()` method checks last word before period
- [ ] Single-letter abbreviations handled (J. K. Rowling)
- [ ] Decimal numbers not split (3.14, $99.99)

### Frontend
- [ ] NotFound.jsx page created with link back to Library
- [ ] App.jsx has catch-all `<Route path="*">` at end of routes
- [ ] 404 page renders correctly

### Testing Requirements

1. **Speed Control Tests:**
   - [ ] `test_apply_speed_2x`: 2x speed → duration halved
   - [ ] `test_apply_speed_half`: 0.5x → duration doubled
   - [ ] `test_apply_speed_1x`: 1.0x → unchanged

2. **Normalization Tests:**
   - [ ] `test_normalize_quiet_audio`: quiet audio boosted to -18 dBFS
   - [ ] `test_normalize_loud_audio`: loud audio reduced
   - [ ] `test_normalize_peak_limiter`: peaks never exceed -0.5 dBFS
   - [ ] `test_normalize_silent_audio`: silent audio returned unchanged

3. **Chunk Validation Tests:**
   - [ ] `test_validate_chunk_too_short`: < 100ms raises ValueError
   - [ ] `test_validate_chunk_too_long`: > 120s raises ValueError
   - [ ] `test_validate_chunk_silent`: < -55 dBFS raises ValueError
   - [ ] `test_validate_chunk_clipping`: > -0.1 dBFS raises ValueError
   - [ ] `test_validate_chunk_valid`: normal audio passes

4. **Chunker Abbreviation Tests:**
   - [ ] `test_chunker_dr_smith`: "Dr. Smith went home." → one sentence
   - [ ] `test_chunker_decimal`: "The value is 3.14." → one sentence
   - [ ] `test_chunker_initials`: "J. K. Rowling wrote it." → one sentence
   - [ ] `test_chunker_normal_split`: "Hello. Goodbye." → two sentences

5. **Frontend Tests:**
   - [ ] `test_404_route`: navigating to /nonexistent shows NotFound
   - [ ] `test_404_link_to_home`: NotFound has link to "/"

---

## File Structure

```
src/
  engines/
    qwen3_tts.py                    # MODIFIED: speed fix, normalization peak limiter, timeout
    chunker.py                      # MODIFIED: abbreviation handling
  pipeline/
    generator.py                    # MODIFIED: chunk validation, retry logic
  config.py                         # MODIFIED: chunk_timeout_seconds setting
  health_checks.py                  # MODIFIED: file cleanup try-except

frontend/src/
  pages/
    NotFound.jsx                    # NEW: 404 page
  App.jsx                           # MODIFIED: add catch-all route

tests/
  test_speed_normalization.py       # NEW: speed and normalization tests
  test_chunk_validation.py          # NEW: chunk validation tests
  test_chunker_abbreviations.py     # NEW: abbreviation handling tests
```

---

## Commit Message

```
[PROMPT-17] Production hardening — critical bug fixes and safety systems

- Fix speed control with clarifying variable and comment
- Add peak limiter to audio normalization (prevent clipping above -0.5 dBFS)
- Add chunk-level audio validation before stitching (duration, silence, clipping)
- Add generation timeout per chunk (configurable, default 120s)
- Fix sentence chunker abbreviation handling (Dr., Mr., 3.14, etc.)
- Add 404 catch-all route in frontend
- Fix health check file cleanup permission error
- Comprehensive tests for all changes
```

---

**Status:** READY FOR IMPLEMENTATION
**Estimated Effort:** 6-8 hours
**Dependencies:** All previous prompts (01-16)
