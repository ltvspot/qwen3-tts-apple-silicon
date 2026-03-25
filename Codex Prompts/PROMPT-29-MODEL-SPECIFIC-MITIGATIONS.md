# PROMPT-29: Qwen3-TTS Model-Specific Mitigations

## Context
This prompt addresses known failure modes specific to the Qwen3-TTS 1.7B CustomVoice 8-bit model running on Apple Silicon via MLX. These are documented issues from the model's GitHub, community reports, and our own testing. Each mitigation targets a specific model behavior.

---

## Task 1: Adaptive Chunk Timeout

### Problem
The current 120-second chunk timeout is static. For a short chunk (50 characters, expected 2 seconds of audio), 120 seconds of waiting means the model has entered an infinite generation loop — but the system doesn't know until the timeout fires. Meanwhile, it's generated 2 minutes of garbage audio. For a long chunk (500 characters, expected 30 seconds), 120 seconds might actually be too short on slow hardware.

### Implementation

In `src/pipeline/generator.py` or `src/engines/qwen3_tts.py`:

```python
def compute_adaptive_timeout(text: str, speed: float = 1.0) -> float:
    """Compute timeout based on expected audio duration.

    Formula: timeout = max(MIN_TIMEOUT, expected_duration * TIMEOUT_MULTIPLIER)
    """
    MIN_TIMEOUT = 15.0  # Never less than 15 seconds
    MAX_TIMEOUT = 300.0  # Never more than 5 minutes
    TIMEOUT_MULTIPLIER = 4.0  # 4x expected duration

    word_count = len(text.split())
    expected_seconds = (word_count / 2.5) / speed  # 2.5 words/sec at 1.0x
    timeout = max(MIN_TIMEOUT, expected_seconds * TIMEOUT_MULTIPLIER)
    return min(timeout, MAX_TIMEOUT)
```

Replace the fixed timeout in `generate_chunk_with_timeout()`:
```python
async def generate_chunk_with_timeout(self, text, voice, emotion, speed):
    timeout = compute_adaptive_timeout(text, speed)
    return await asyncio.wait_for(
        asyncio.to_thread(self.generate, text, voice, emotion, speed),
        timeout=timeout,
    )
```

Also add a **max audio duration** hard cap in `chunk_validator.py`:
```python
def check_max_audio_duration(audio: AudioSegment, input_text: str, speed: float = 1.0) -> ValidationResult:
    """Reject audio that's more than 2x the expected duration (infinite loop indicator)."""
    word_count = len(input_text.split())
    expected_seconds = (word_count / 2.5) / speed
    max_allowed = max(10.0, expected_seconds * 2.0)  # At least 10s, max 2x expected

    if len(audio) / 1000 > max_allowed:
        return ValidationResult(
            check="max_audio_duration",
            severity=ValidationSeverity.FAIL,
            message=f"Audio {len(audio)/1000:.1f}s exceeds 2x expected {expected_seconds:.1f}s — likely infinite loop",
        )
    return ValidationResult(check="max_audio_duration", severity=ValidationSeverity.PASS, message="OK")
```

---

## Task 2: Per-Chunk Max-Pause Trimmer

### Problem
The 1.7B model occasionally inserts 2-27 second silences mid-generation. While the 1.7B is much better than the 0.6B (2 excessive pauses vs 106 in testing), it still happens. These silences pass per-chunk validation because the overall duration isn't wildly off — but they create 2-5 second dead spots in the audio.

### Implementation

Create `src/pipeline/pause_trimmer.py`:

```python
"""Detect and trim excessive mid-chunk silences to natural lengths."""

from pydub import AudioSegment
from pydub.silence import detect_silence

MAX_PAUSE_MS = 1500  # Maximum allowed pause within a single chunk
TRIM_TARGET_MS = 800  # Trim excessive pauses to this length
EDGE_PRESERVE_MS = 400  # Preserve this much silence at each edge of the trim

class PauseTrimmer:
    """Trims excessively long silences within audio chunks."""

    @classmethod
    def trim_excessive_pauses(
        cls,
        audio: AudioSegment,
        max_pause_ms: int = MAX_PAUSE_MS,
        trim_target_ms: int = TRIM_TARGET_MS,
        silence_thresh_db: int = -40,
    ) -> tuple[AudioSegment, int]:
        """
        Detect silences longer than max_pause_ms and trim them.

        Returns:
            (trimmed_audio, num_pauses_trimmed)
        """
        silences = detect_silence(
            audio,
            min_silence_len=max_pause_ms,
            silence_thresh=silence_thresh_db,
        )

        if not silences:
            return audio, 0

        # Process silences from end to start (so indices don't shift)
        trimmed = audio
        count = 0
        for start_ms, end_ms in reversed(silences):
            silence_duration = end_ms - start_ms
            if silence_duration > max_pause_ms:
                # Keep edge_preserve at each side, remove the middle
                keep_before = start_ms + EDGE_PRESERVE_MS
                keep_after = end_ms - EDGE_PRESERVE_MS

                if keep_before < keep_after:
                    # Remove the middle section
                    trimmed = trimmed[:keep_before] + trimmed[keep_after:]
                    count += 1

        return trimmed, count
```

Wire into `generator.py` after chunk generation, before validation:
```python
# After generating chunk audio:
audio_chunk, pauses_trimmed = PauseTrimmer.trim_excessive_pauses(audio_chunk)
if pauses_trimmed > 0:
    logger.info(f"Chunk {i}: trimmed {pauses_trimmed} excessive pauses")
```

---

## Task 3: First-Token Phoneme Bleed Fix (Voice Cloning)

### Problem
In voice cloning mode, the model's first generated token conditions on whatever phoneme the reference audio ends on. This causes a brief artifact at the start of generated speech — a half-syllable that doesn't belong.

### Implementation

In `src/engines/qwen3_tts.py`, in the voice cloning generation path:

```python
def _prepare_reference_audio(self, ref_audio_path: str) -> str:
    """Prepare reference audio for cloning — append silence to prevent phoneme bleed."""
    from pydub import AudioSegment

    ref = AudioSegment.from_file(ref_audio_path)

    # Append 500ms silence to prevent first-token phoneme bleed
    silence = AudioSegment.silent(duration=500, frame_rate=ref.frame_rate)
    padded = ref + silence

    # Save to temp file
    import tempfile
    temp_path = tempfile.mktemp(suffix=".wav")
    padded.export(temp_path, format="wav")
    return temp_path
```

Call this before passing the reference audio to MLX:
```python
# In _generate_cloned_audio():
prepared_ref = self._prepare_reference_audio(ref_audio_path)
try:
    result = self._cloned_model.generate(text=text, ref_audio=prepared_ref, ...)
finally:
    os.unlink(prepared_ref)  # Clean up temp file
```

Also trim the first 100ms of generated cloned audio if it contains a transient spike:
```python
def _trim_phoneme_bleed(audio: AudioSegment, threshold_db: float = -20) -> AudioSegment:
    """Remove potential phoneme bleed artifact from start of cloned audio."""
    head = audio[:100]  # First 100ms
    if head.dBFS > threshold_db:  # Suspiciously loud for start of speech
        # Check if it's a transient (energy drops quickly)
        first_50 = audio[:50].dBFS
        next_50 = audio[50:100].dBFS
        if first_50 > next_50 + 6:  # 6dB drop in 50ms = transient, not speech
            return audio[80:]  # Trim 80ms
    return audio
```

---

## Task 4: Post-Reload Quality Canary

### Problem
After model reload (triggered by memory thresholds), audio quality may subtly degrade — the model produces slightly more metallic-sounding or less natural audio for the first few chunks. There's no detection for this.

### Implementation

In `src/engines/model_manager.py`, after a reload completes:

```python
CANARY_TEXT = "The old lighthouse keeper closed his journal and set it on the windowsill."
_baseline_spectral_centroid = None  # Set on first successful canary

async def _run_quality_canary(self):
    """Generate a test phrase after reload and verify quality matches baseline."""
    global _baseline_spectral_centroid

    try:
        engine = self._engine
        if engine is None:
            return

        # Generate canary audio
        audio = await asyncio.to_thread(
            engine.generate, CANARY_TEXT, "Ethan", "neutral", 1.0
        )

        if audio is None or len(audio) < 1000:
            logger.warning("Quality canary: generation failed, triggering another reload")
            await self._reload_engine()
            return

        # Compute spectral centroid
        import numpy as np
        samples = np.array(audio.get_array_of_samples(), dtype=np.float64)
        fft = np.fft.rfft(samples)
        freqs = np.fft.rfftfreq(len(samples), 1/audio.frame_rate)
        magnitude = np.abs(fft)
        centroid = np.sum(freqs * magnitude) / np.sum(magnitude)

        if _baseline_spectral_centroid is None:
            # First run — establish baseline
            _baseline_spectral_centroid = centroid
            logger.info(f"Quality canary: baseline spectral centroid = {centroid:.1f} Hz")
        else:
            # Compare to baseline
            deviation = abs(centroid - _baseline_spectral_centroid) / _baseline_spectral_centroid
            if deviation > 0.15:  # >15% deviation
                logger.warning(f"Quality canary: spectral centroid {centroid:.1f} Hz "
                             f"deviates {deviation*100:.1f}% from baseline "
                             f"{_baseline_spectral_centroid:.1f} Hz — triggering re-reload")
                await self._reload_engine()
            else:
                logger.info(f"Quality canary: OK (deviation {deviation*100:.1f}%)")

    except Exception as e:
        logger.error(f"Quality canary failed: {e}")
```

Call `_run_quality_canary()` after every model reload in `get_engine()`.

---

## Task 5: Lower Memory Threshold & Longer Reload Delay

### Problem
Memory threshold at 12GB is too late — degradation starts around 10GB on a 16GB Mac. The 1-second post-reload delay isn't enough for MLX to fully release unified memory.

### Implementation

In `src/config.py`:
```python
# Change default from 12000.0 to 10000.0
memory_pressure_threshold_mb: float = Field(
    default=10000.0, ge=4000.0, le=32000.0,
    description="Trigger model reload when process memory exceeds this (MB)"
)
```

In `src/engines/model_manager.py`, in the reload method:
```python
async def _reload_engine(self):
    """Reload the engine with proper memory cleanup."""
    if self._engine:
        self._engine = None

    # Force garbage collection
    import gc
    gc.collect()

    # Wait for MLX to release unified memory (3 seconds, not 1)
    await asyncio.sleep(3.0)

    # Additional GC pass
    gc.collect()

    # Reload engine
    self._engine = await self._create_engine()

    # Run quality canary
    await self._run_quality_canary()
```

---

## Task 6: Enforce English Language Code

### Problem
If `lang_code` is not explicitly set to `"en"`, the model may default to Mandarin phoneme rules, causing subtle Chinese accent artifacts on English text.

### Implementation

In `src/engines/qwen3_tts.py`, in the generate method:
```python
# Ensure English language code is always set
lang_code = "en"  # Hardcoded for English audiobook production

# In _generate_mlx_audio():
results = self._model.generate(
    text=text,
    voice=speaker_id,
    instruct=style_instruction,
    speed=1.0,
    lang_code=lang_code,  # ALWAYS "en"
)
```

Verify this is passed in ALL generation paths: regular, voice cloning, and preview.

---

## Task 7: Pronunciation Watchlist

### Problem
Certain English words consistently trigger pronunciation artifacts (foreign names, technical terms, archaic words). There's no way to flag these proactively.

### Implementation

Create `src/pipeline/pronunciation_watchlist.py`:

```python
"""Track words known to cause pronunciation issues with Qwen3-TTS."""

import re
import json
from pathlib import Path

WATCHLIST_PATH = Path("data/pronunciation_watchlist.json")

# Default watchlist
DEFAULT_WATCHLIST = {
    # Words with unusual stress patterns
    "hyperbole": "hy-PER-bo-lee",
    "epitome": "eh-PIT-oh-mee",
    "quinoa": "KEEN-wah",
    # Technical/archaic terms
    "albeit": "all-BEE-it",
    "segue": "SEG-way",
    "cache": "CASH",
    # Common proper nouns that cause issues
    "Hermione": "her-MY-oh-nee",
    "Versailles": "ver-SIGH",
}

class PronunciationWatchlist:
    def __init__(self):
        self._watchlist = self._load()

    def _load(self) -> dict[str, str]:
        if WATCHLIST_PATH.exists():
            return json.loads(WATCHLIST_PATH.read_text())
        return DEFAULT_WATCHLIST.copy()

    def save(self):
        WATCHLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        WATCHLIST_PATH.write_text(json.dumps(self._watchlist, indent=2))

    def check_text(self, text: str) -> list[dict]:
        """Find watchlist words in text and return warnings."""
        warnings = []
        text_lower = text.lower()
        for word, guide in self._watchlist.items():
            if word.lower() in text_lower:
                warnings.append({
                    "word": word,
                    "pronunciation_guide": guide,
                    "context": "This word is known to cause pronunciation issues with Qwen3-TTS",
                })
        return warnings

    def add_word(self, word: str, guide: str):
        self._watchlist[word] = guide
        self.save()

    def remove_word(self, word: str):
        self._watchlist.pop(word, None)
        self.save()
```

Wire into the pre-generation flow:
- Before generating a chapter, check text against the watchlist
- Log warnings for any matches
- Add matches to chapter review notes (so the QA reviewer knows to listen carefully)

Add an API endpoint to manage the watchlist:
```python
GET /api/pronunciation-watchlist  # List all words
POST /api/pronunciation-watchlist  # Add a word
DELETE /api/pronunciation-watchlist/{word}  # Remove a word
```

---

## Task 8: Tests

Create `tests/test_model_mitigations.py`:
1. `test_adaptive_timeout_short_text` — 50-char text gets ~15s timeout, not 120s
2. `test_adaptive_timeout_long_text` — 500-char text gets ~60s timeout
3. `test_max_audio_duration_rejects_loop` — 2x expected audio flagged as FAIL
4. `test_pause_trimmer_trims_long_silence` — 5-second silence trimmed to 800ms
5. `test_pause_trimmer_preserves_normal_pause` — 1-second silence untouched
6. `test_phoneme_bleed_fix_appends_silence` — reference audio padded with 500ms
7. `test_quality_canary_passes` — normal audio within 15% baseline
8. `test_quality_canary_fails` — degraded audio triggers re-reload
9. `test_pronunciation_watchlist_flags_word` — "hyperbole" in text produces warning
10. `test_english_lang_code_enforced` — generation calls include lang_code="en"

All existing tests must still pass.
