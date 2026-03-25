# PROMPT-23: Intelligent Sentence-Boundary Pause Padding & Voice Defaults

## Context
User testing revealed that inter-sentence pauses in generated audio are sometimes too short — especially after long, dramatic sentences. The TTS engine (Qwen3-TTS) doesn't support SSML `<break>` tags, and the current `AudioStitcher` only does a 30ms crossfade between chunks with **zero silence padding**. This makes transitions between sentences feel rushed.

Additionally, the universal voice defaults must be enforced as **Ethan / neutral / 1.0x** everywhere with no exceptions.

---

## Task 1: Intelligent Sentence-Boundary Pause Padding

### Problem
The `AudioStitcher.stitch()` in `src/engines/chunker.py` (lines 241-260) joins audio chunks with only a 30ms crossfade:
```python
CROSSFADE_MS = 30
result = result.append(chunk, crossfade=crossfade)
```
No silence is inserted between chunks. When a chunk ends mid-paragraph at a sentence boundary, the next sentence starts too quickly.

### Audio Analysis Results
From real user-generated audio (34.6s clip, Ethan/neutral/1.0x):
- Most inter-sentence pauses: 450-650ms (acceptable)
- Pause after a long dramatic sentence: only 600ms (should be 800-1200ms)
- Comma pauses: 200-400ms (correct)
- Professional audiobook standard: 500-800ms between sentences, 800-1200ms after dramatic/long sentences, 1000-1500ms between paragraphs

### Solution: Post-Generation Sentence Pause Analyzer

Create a new class `SentencePauseAnalyzer` that runs after chunk generation but before final stitching. It analyzes the text and audio to determine where additional silence padding is needed.

#### Step 1: Create `src/pipeline/sentence_pause.py`

```python
"""Sentence-boundary pause analysis and padding for natural audiobook pacing."""

from pydub import AudioSegment
from pydub.silence import detect_silence
import re
import math

class SentencePauseAnalyzer:
    """Analyzes and pads silence at sentence boundaries for natural pacing."""

    # Minimum pause durations (milliseconds) by boundary type
    MIN_PAUSE_MS = {
        "comma": 150,           # Within-sentence comma pause
        "sentence": 500,        # Regular sentence boundary (period, ?, !)
        "dramatic": 800,        # After long/dramatic sentences (>120 chars)
        "dialogue_end": 700,    # After closing dialogue quote
        "paragraph": 1000,      # Between paragraphs (double newline in source)
        "chapter_start": 1500,  # After chapter heading
    }

    # Sentences longer than this (chars) get "dramatic" pause treatment
    DRAMATIC_SENTENCE_THRESHOLD = 120

    # Em-dash or ellipsis at end of sentence also triggers dramatic pause
    DRAMATIC_ENDINGS = re.compile(r'[—…]+["\']?\s*$')

    # Silence detection threshold (dBFS)
    SILENCE_THRESHOLD_DB = -40

    @classmethod
    def classify_boundary(cls, preceding_text: str, following_text: str) -> str:
        """Classify the type of boundary between two text segments."""
        preceding = preceding_text.rstrip()

        # Paragraph boundary (double newline)
        if "\n\n" in preceding_text[-5:] or "\n\n" in following_text[:5]:
            return "paragraph"

        # Dialogue ending
        if preceding.endswith('"') or preceding.endswith("'") or preceding.endswith('\u201d'):
            return "dialogue_end"

        # Dramatic sentence (long or ends with em-dash/ellipsis)
        if len(preceding) > cls.DRAMATIC_SENTENCE_THRESHOLD:
            return "dramatic"
        if cls.DRAMATIC_ENDINGS.search(preceding):
            return "dramatic"

        # Regular sentence boundary
        if preceding and preceding[-1] in ".!?":
            return "sentence"

        # Comma or other mid-sentence break
        return "comma"

    @classmethod
    def measure_trailing_silence(cls, audio: AudioSegment) -> int:
        """Measure silence duration at the end of an audio segment (ms)."""
        # Check last 2 seconds max
        tail = audio[-2000:] if len(audio) > 2000 else audio
        silences = detect_silence(tail, min_silence_len=50, silence_thresh=cls.SILENCE_THRESHOLD_DB)
        if not silences:
            return 0
        # Find the last silence region that extends to the end of the audio
        last_silence = silences[-1]
        tail_start = max(0, len(audio) - 2000)
        silence_end = tail_start + last_silence[1]
        # Only count if it reaches the end of the audio
        if silence_end >= len(audio) - 50:  # within 50ms of end
            return last_silence[1] - last_silence[0]
        return 0

    @classmethod
    def measure_leading_silence(cls, audio: AudioSegment) -> int:
        """Measure silence duration at the start of an audio segment (ms)."""
        head = audio[:2000] if len(audio) > 2000 else audio
        silences = detect_silence(head, min_silence_len=50, silence_thresh=cls.SILENCE_THRESHOLD_DB)
        if not silences:
            return 0
        first_silence = silences[0]
        if first_silence[0] <= 50:  # starts within 50ms of beginning
            return first_silence[1] - first_silence[0]
        return 0

    @classmethod
    def compute_padding(
        cls,
        text_chunks: list[str],
        audio_chunks: list[AudioSegment],
    ) -> list[int]:
        """
        Compute how many milliseconds of silence to insert BETWEEN each pair
        of adjacent audio chunks.

        Returns a list of length len(audio_chunks) - 1.
        Each value is the additional silence to insert (0 = no extra padding needed).
        """
        if len(audio_chunks) <= 1:
            return []

        paddings = []
        for i in range(len(audio_chunks) - 1):
            boundary_type = cls.classify_boundary(text_chunks[i], text_chunks[i + 1])
            min_pause = cls.MIN_PAUSE_MS.get(boundary_type, 500)

            # Measure existing silence at boundary
            trailing = cls.measure_trailing_silence(audio_chunks[i])
            leading = cls.measure_leading_silence(audio_chunks[i + 1])
            existing_pause = trailing + leading

            # Only add padding if existing pause is shorter than minimum
            needed = max(0, min_pause - existing_pause)
            paddings.append(needed)

        return paddings

    @classmethod
    def generate_silence(cls, duration_ms: int, sample_rate: int = 24000) -> AudioSegment:
        """Generate a silent AudioSegment of the specified duration."""
        return AudioSegment.silent(duration=duration_ms, frame_rate=sample_rate)
```

#### Step 2: Integrate into AudioStitcher

Modify `AudioStitcher.stitch()` in `src/engines/chunker.py` to accept optional padding:

```python
@staticmethod
def stitch(
    audio_chunks: list[AudioSegment],
    text_chunks: list[str] | None = None,
    apply_sentence_padding: bool = True,
) -> AudioSegment:
    """Stitch audio chunks with optional intelligent sentence-boundary padding."""
    if not audio_chunks:
        raise ValueError("No audio chunks to stitch")
    if len(audio_chunks) == 1:
        return audio_chunks[0]

    # Compute padding if text chunks are provided
    paddings = []
    if apply_sentence_padding and text_chunks and len(text_chunks) == len(audio_chunks):
        from src.pipeline.sentence_pause import SentencePauseAnalyzer
        paddings = SentencePauseAnalyzer.compute_padding(text_chunks, audio_chunks)
    else:
        paddings = [0] * (len(audio_chunks) - 1)

    result = audio_chunks[0]
    for i, chunk in enumerate(audio_chunks[1:]):
        padding_ms = paddings[i] if i < len(paddings) else 0

        if padding_ms > 0:
            # Insert silence padding, then append with minimal crossfade
            silence = AudioSegment.silent(
                duration=padding_ms,
                frame_rate=chunk.frame_rate,
            )
            result = result + silence
            crossfade = min(CROSSFADE_MS, len(result), len(chunk))
            result = result.append(chunk, crossfade=crossfade)
        else:
            # Original behavior — just crossfade
            crossfade = min(CROSSFADE_MS, len(result), len(chunk))
            result = result.append(chunk, crossfade=crossfade)

    return result
```

#### Step 3: Wire into generator.py

In `generate_chapter()`, pass `text_chunks` to the stitcher:

Find the line where `AudioStitcher.stitch(audio_chunks)` is called and change it to:
```python
final_audio = AudioStitcher.stitch(audio_chunks, text_chunks=text_chunks)
```

Make sure the `text_chunks` list (the original text strings for each chunk) is preserved alongside `audio_chunks` through the generation loop.

#### Step 4: Add configurable pause settings

Add to `src/config.py` in `RuntimeSettings` or `ApplicationSettings`:
```python
class PauseSettings(BaseModel):
    """Inter-sentence pause configuration."""
    sentence_pause_ms: int = Field(default=500, ge=100, le=2000)
    dramatic_pause_ms: int = Field(default=800, ge=200, le=3000)
    paragraph_pause_ms: int = Field(default=1000, ge=300, le=5000)
    dialogue_end_pause_ms: int = Field(default=700, ge=200, le=2000)
    enabled: bool = Field(default=True)
```

Wire these settings into `SentencePauseAnalyzer` so they can be tuned without code changes.

---

## Task 2: Voice Defaults Hardcoded Everywhere

The universal defaults are now: **voice="Ethan", emotion="neutral", speed=1.0**

Verify and enforce these defaults in ALL of the following locations. Some have already been updated — check each one and fix any that don't match:

### Backend (Python):
1. `src/config.py` — `VoiceSettings` class: name="Ethan", emotion="neutral", speed=1.0
2. `src/database.py` — `GenerationJob` model: voice_name default="Ethan", emotion default="neutral" (NOT nullable), speed default=1.0
3. `src/pipeline/generator.py` — `generate_book()` and `generate_chapter()`: voice_name="Ethan", emotion="neutral", speed=1.0
4. `src/api/voice_lab.py` — `VoiceTestRequest`: voice="Ethan", emotion="neutral", speed=1.0
5. `src/api/queue_routes.py` — batch endpoint defaults via `get_application_settings().default_voice`

### Frontend (JavaScript/JSX):
6. `frontend/src/pages/BookDetail.jsx` — `DEFAULT_NARRATION_SETTINGS`: voice="Ethan", emotion="neutral", speed=1.0
7. `frontend/src/pages/VoiceLab.jsx` — useState defaults: voice="Ethan", emotion="neutral", speed=1.0
8. `frontend/src/pages/Queue.jsx` — `BATCH_DEFAULTS`: voice="Ethan", emotion="neutral", speed=1.0
9. `frontend/src/components/NarrationSettings.jsx` — fallback defaults

**IMPORTANT**: The `emotion` field in the database model (`src/database.py`) must be `nullable=False, default="neutral"` — NOT nullable. Every generation job must have an emotion set.

---

## Task 3: Voice Preview Pause Settings

Add a "Sentence Padding" toggle to the Voice Lab preview UI so users can hear the difference:

In `VoiceLab.jsx`, add a small toggle below the Speed slider:
```
[x] Natural sentence pauses (recommended)
```

When enabled (default: on), the preview request should include `apply_sentence_padding: true`.

Update the `/api/voice-lab/test` endpoint to accept this parameter and pass it through to the generation pipeline.

---

## Task 4: QA Check for Pause Quality

Add a new QA check in `src/pipeline/qa_checker.py`:

**`check_sentence_pacing()`**:
- Analyze the generated chapter audio for inter-sentence pauses
- Flag WARNING if any sentence boundary has < 300ms pause
- Flag WARNING if any pause exceeds 3000ms (unnaturally long)
- Flag INFO with statistics: min/max/avg/median pause durations
- This helps the QA overseer catch pacing issues before export

---

## Task 5: Tests

1. **`tests/test_sentence_pause.py`** — Unit tests for `SentencePauseAnalyzer`:
   - `test_classify_boundary_sentence` — period at end → "sentence"
   - `test_classify_boundary_dramatic` — long sentence (>120 chars) → "dramatic"
   - `test_classify_boundary_dialogue` — ends with closing quote → "dialogue_end"
   - `test_classify_boundary_paragraph` — double newline → "paragraph"
   - `test_compute_padding_no_extra_needed` — when existing silence exceeds minimum
   - `test_compute_padding_adds_silence` — when existing silence is too short
   - `test_stitch_with_padding` — AudioStitcher produces longer output with padding enabled

2. **`tests/test_qa_pacing.py`** — QA check for pacing:
   - `test_check_sentence_pacing_good` — well-paced audio passes
   - `test_check_sentence_pacing_too_short` — rushes flagged as warning

3. All existing tests must still pass.

4. **Rebuild the frontend** after all changes: `cd frontend && npm run build`

---

## Priority Order
1. Task 1 (Sentence pause analyzer — core fix)
2. Task 2 (Voice defaults verification — quick)
3. Task 4 (QA pacing check — catches future issues)
4. Task 3 (Voice Lab toggle — nice to have)
5. Task 5 (Tests)

Run `cd frontend && npm run build` after all frontend changes.
