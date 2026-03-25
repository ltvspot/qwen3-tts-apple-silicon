# PROMPT-24: Quality Gate 1 — Per-Chunk Validation (Hallucination, Repeats, Alignment)

## Context
This is part of a three-gate quality pipeline to guarantee 100% perfect audiobook audio. Gate 1 runs **immediately after each chunk is generated**, before stitching. It catches problems at the smallest unit so bad audio never makes it into the final chapter.

The existing `ChunkValidator` (in `src/pipeline/chunk_validator.py`) checks duration, silence floor, clipping, sample rate, and a basic hallucination heuristic (duration >3x expected). This prompt expands it with much more rigorous checks.

**IMPORTANT**: All new checks should be non-blocking by default (log warnings, flag for review) but configurable to be blocking (reject chunk and trigger regeneration). Use a `ValidationSeverity` enum: `INFO`, `WARNING`, `FAIL`. Only `FAIL` triggers automatic regeneration.

---

## Task 1: Speech-to-Text Alignment Check

### Problem
The model can skip words, add hallucinated words, or read punctuation literally. There is currently NO verification that the audio actually matches the input text.

### Implementation

Add to `src/pipeline/chunk_validator.py`:

**`check_text_alignment(audio: AudioSegment, input_text: str) -> ValidationResult`**

1. Use Python's `whisper` library (OpenAI Whisper tiny/base model) for fast local speech-to-text:
   - Install: `pip install openai-whisper` (or use `faster-whisper` for speed)
   - Use the `tiny.en` model — it's fast enough for per-chunk validation (~1s per chunk)
   - Transcribe the audio chunk to text
2. Normalize both texts: lowercase, strip punctuation, collapse whitespace
3. Compute word error rate (WER) using Levenshtein distance at the word level:
   ```python
   def word_error_rate(reference: str, hypothesis: str) -> float:
       ref_words = reference.lower().split()
       hyp_words = hypothesis.lower().split()
       # Standard WER: (substitutions + insertions + deletions) / len(reference)
       # Use dynamic programming edit distance
   ```
4. Thresholds:
   - WER < 0.15 (15%): `PASS` — normal TTS variation in pronunciation
   - WER 0.15–0.30: `WARNING` — flag for manual review, include both texts in report
   - WER > 0.30: `FAIL` — trigger automatic regeneration

**IMPORTANT**: If Whisper is not available (import fails), gracefully degrade:
- Log a warning that STT alignment is disabled
- Return `INFO` status with message "STT alignment check skipped (whisper not installed)"
- Never crash the pipeline if Whisper isn't installed

**Fallback if Whisper is too heavy**: Use a simpler heuristic:
- Estimate expected word count from input text
- Estimate actual word count from audio duration (avg 2.5 words/sec for narration)
- If actual/expected ratio is outside 0.6–1.4 range, flag as WARNING

### Configuration
Add to `src/config.py`:
```python
class ChunkValidationSettings(BaseModel):
    stt_alignment_enabled: bool = Field(default=True)
    stt_model: str = Field(default="tiny.en")  # whisper model size
    wer_warning_threshold: float = Field(default=0.15)
    wer_fail_threshold: float = Field(default=0.30)
```

---

## Task 2: Repeat/Loop Detection

### Problem
Small TTS models frequently hallucinate repeating phrases — saying the same 3-8 words two or three times in a row. The current duration heuristic misses this when the repeat only adds 2-3 seconds.

### Implementation

Add to `src/pipeline/chunk_validator.py`:

**`check_repeats(audio: AudioSegment, input_text: str) -> ValidationResult`**

**Approach A — Text-based (preferred, requires STT from Task 1):**
1. If STT transcript is available, scan for repeated n-grams:
   ```python
   def detect_repeated_phrases(transcript: str, min_ngram: int = 3, max_ngram: int = 8) -> list[str]:
       words = transcript.lower().split()
       repeats = []
       for n in range(min_ngram, max_ngram + 1):
           for i in range(len(words) - 2 * n + 1):
               phrase = words[i:i+n]
               next_phrase = words[i+n:i+2*n]
               if phrase == next_phrase:
                   repeats.append(" ".join(phrase))
       return repeats
   ```
2. If any repeated phrase found of 3+ words: `FAIL` — trigger regeneration
3. Repeated 2-word phrases: `WARNING` (could be intentional, e.g., "very, very")

**Approach B — Audio-based (fallback, no STT needed):**
1. Compute audio fingerprint using windowed auto-correlation:
   - Split chunk into 500ms overlapping windows (250ms hop)
   - Compute normalized cross-correlation between consecutive windows
   - If correlation > 0.85 for 3+ consecutive window pairs, it's likely a repeat
2. Threshold: correlation > 0.85 for >1.5 seconds: `WARNING`
3. Correlation > 0.90 for >2.0 seconds: `FAIL`

### Configuration
```python
repeat_detection_enabled: bool = Field(default=True)
min_repeat_ngram: int = Field(default=3)
repeat_correlation_threshold: float = Field(default=0.85)
```

---

## Task 3: Phoneme Confidence / Gibberish Detection

### Problem
When the model encounters unusual words, it can produce garbled audio — mumbling, slurred phonemes, or outright gibberish. This is especially common with: proper nouns, non-English words, archaic language, technical terms, and numbers/dates.

### Implementation

Add to `src/pipeline/chunk_validator.py`:

**`check_audio_clarity(audio: AudioSegment) -> ValidationResult`**

1. **Spectral clarity metric**: Compute spectral flatness (Wiener entropy) over the speech regions:
   ```python
   import numpy as np

   def spectral_flatness(signal, sample_rate, frame_size=2048, hop_size=512):
       """Lower flatness = more tonal/clear speech; higher = more noise-like/garbled."""
       # Compute STFT magnitude spectrum
       # For each frame: geometric_mean(spectrum) / arithmetic_mean(spectrum)
       # Average across all frames
   ```
   - Clean speech: spectral flatness typically 0.01–0.15
   - Garbled/noisy: spectral flatness > 0.25
   - Threshold: average flatness > 0.20 over speech frames: `WARNING`

2. **Zero-crossing rate analysis**: Gibberish often has erratic zero-crossing patterns:
   - Compute ZCR in 50ms windows
   - If standard deviation of ZCR across windows > 2x the mean: `WARNING` (unstable articulation)

3. **Energy continuity**: Garbled audio often has erratic energy fluctuations:
   - Compute RMS in 100ms windows
   - If more than 3 windows have energy drops > 20dB from neighbors: `WARNING`

### Configuration
```python
clarity_check_enabled: bool = Field(default=True)
spectral_flatness_warning: float = Field(default=0.20)
spectral_flatness_fail: float = Field(default=0.30)
```

---

## Task 4: Enhanced Duration Validation

### Problem
The current duration check uses a simple 3x heuristic. This misses subtle issues.

### Implementation

Replace the existing duration check in `ChunkValidator` with a more nuanced version:

**`check_duration_detailed(audio: AudioSegment, input_text: str) -> ValidationResult`**

1. Estimate expected duration based on text analysis:
   ```python
   def estimate_duration(text: str, speed: float = 1.0) -> tuple[float, float]:
       """Returns (min_expected_seconds, max_expected_seconds)."""
       word_count = len(text.split())
       # Average narration: 150 words/minute = 2.5 words/second
       # But varies by content type:
       base_wps = 2.5 / speed

       # Dialogue tends to be faster
       dialogue_ratio = count_dialogue_chars(text) / max(len(text), 1)
       # Descriptive prose tends to be slower

       avg_duration = word_count / base_wps
       # Add pause time: ~0.5s per sentence
       sentence_count = len(re.split(r'[.!?]+', text))
       pause_time = sentence_count * 0.5

       expected = avg_duration + pause_time
       return (expected * 0.6, expected * 1.8)  # Allow 60%-180% range
   ```

2. Thresholds:
   - Within expected range: `PASS`
   - 10-40% outside range: `WARNING` with details
   - >40% outside range: `FAIL`

---

## Task 5: Automatic Regeneration on FAIL

### Problem
Currently, chunk validation failures are logged but the bad audio is still included. Chunks that `FAIL` should be automatically regenerated.

### Implementation

Modify `src/pipeline/generator.py` in the chunk generation loop:

```python
# After generating a chunk:
validation = chunk_validator.validate(audio_chunk, text_chunk, voice, speed)

if validation.severity == ValidationSeverity.FAIL and attempt < max_attempts:
    logger.warning(f"Chunk {i} failed validation ({validation.check}: {validation.message}), "
                   f"regenerating (attempt {attempt + 1}/{max_attempts})")
    # Add small random variation to avoid identical regeneration
    # Option: slightly adjust speed (±0.02) or add/remove a trailing space
    continue  # retry the chunk

if validation.severity == ValidationSeverity.FAIL and attempt >= max_attempts:
    logger.error(f"Chunk {i} failed validation after {max_attempts} attempts, "
                 f"marking for manual review")
    chapter.needs_manual_review = True
    chapter.review_notes += f"\nChunk {i} FAILED: {validation.check} — {validation.message}"
```

**Key behavior:**
- `PASS`: continue normally
- `WARNING`: log it, add to chapter review notes, but include the audio
- `FAIL`: regenerate up to 3 times. If still failing after 3 attempts, include the audio but flag the chapter for mandatory manual review

---

## Task 6: Validation Result Model

Create a structured validation result system:

```python
# In src/pipeline/chunk_validator.py

from enum import Enum
from dataclasses import dataclass

class ValidationSeverity(Enum):
    PASS = "pass"
    INFO = "info"
    WARNING = "warning"
    FAIL = "fail"

@dataclass
class ValidationResult:
    check: str           # e.g., "text_alignment", "repeat_detection"
    severity: ValidationSeverity
    message: str         # Human-readable description
    details: dict | None = None  # Optional structured data (WER score, timestamps, etc.)

@dataclass
class ChunkValidationReport:
    chunk_index: int
    text: str
    duration_ms: int
    results: list[ValidationResult]

    @property
    def worst_severity(self) -> ValidationSeverity:
        """Return the most severe result across all checks."""
        severity_order = [ValidationSeverity.PASS, ValidationSeverity.INFO,
                         ValidationSeverity.WARNING, ValidationSeverity.FAIL]
        worst = ValidationSeverity.PASS
        for r in self.results:
            if severity_order.index(r.severity) > severity_order.index(worst):
                worst = r.severity
        return worst

    @property
    def needs_regeneration(self) -> bool:
        return self.worst_severity == ValidationSeverity.FAIL
```

---

## Task 7: Tests

Create `tests/test_chunk_quality_gate.py`:

1. `test_text_alignment_pass` — matching audio/text scores < 0.15 WER
2. `test_text_alignment_fail` — mismatched audio/text scores > 0.30 WER
3. `test_repeat_detection_finds_repeated_phrase` — "the cat sat the cat sat" flagged
4. `test_repeat_detection_allows_intentional` — "very, very" not flagged
5. `test_gibberish_detection_clean_audio` — normal speech passes clarity check
6. `test_duration_detailed_within_range` — expected duration passes
7. `test_duration_detailed_too_long` — 3x expected fails
8. `test_regeneration_on_fail` — mock generator retries on FAIL severity
9. `test_graceful_whisper_missing` — returns INFO when whisper not installed
10. `test_validation_report_worst_severity` — report correctly identifies worst result

All existing tests must still pass.

---

## Dependencies

If Whisper is used:
```
pip install openai-whisper  # or faster-whisper for speed
```

Add to requirements.txt but make it optional:
```
# Optional: enables speech-to-text alignment validation
# openai-whisper>=20231117
```

The pipeline must work WITHOUT Whisper installed — it just skips the STT alignment check.

---

## Priority Order
1. Task 6 (ValidationResult model — everything depends on it)
2. Task 4 (Enhanced duration — improves existing check)
3. Task 2 (Repeat detection — most common failure mode)
4. Task 3 (Gibberish/clarity — catches garbled output)
5. Task 1 (STT alignment — most thorough but heaviest)
6. Task 5 (Auto-regeneration wiring)
7. Task 7 (Tests)
