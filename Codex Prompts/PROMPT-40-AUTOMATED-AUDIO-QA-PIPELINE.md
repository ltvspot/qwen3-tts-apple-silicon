# PROMPT-40: Automated Audio QA Pipeline (AI-Powered Audiobook Review)

**Priority:** HIGH
**Scope:** New module `src/pipeline/audio_qa/` + API endpoints + frontend dashboard
**Branch:** `master`
**Estimated effort:** 5 tasks

---

## Context

Currently, audio quality assessment is limited to basic checks (silence detection, LUFS measurement, clipping). There is no way to verify that the TTS output actually matches the source text, catch mispronunciations, detect hallucinations/looping, or assess overall naturalness — without a human listening to every chapter.

This prompt adds an automated "AI listener" that reviews every generated chapter like a human QA reviewer would, checking word accuracy, pacing, pronunciation, naturalness, and audio artifacts. This is essential for producing 873 books at scale with reliable quality.

---

## Architecture Overview

```
Generated Audio + Source Text
    ↓
[1] Transcription (mlx-whisper)
    ├→ Compare with source text → Word Error Rate (WER)
    └→ Identify mismatches → skipped words, hallucinated words, mispronunciations
    ↓
[2] Timing Analysis (librosa)
    ├→ Pause detection & categorization (natural vs awkward)
    ├→ Speech rate consistency (words per minute)
    └→ Duration vs expected ratio
    ↓
[3] Audio Quality Analysis (librosa + pyloudnorm)
    ├→ LUFS measurement per chapter
    ├→ Dynamic range (LRA)
    ├→ Click/pop detection (spectral anomalies)
    └→ SNR estimation
    ↓
[4] Quality Scoring
    ├→ Per-chapter score (0-100)
    ├→ Per-book aggregate score
    └→ Auto-approve if score > threshold, flag for review if below
    ↓
[Output] QA Report stored in DB + API + Dashboard
```

---

## Task 1: Install Dependencies and Create Module Structure

### Dependencies to add to requirements.txt:
```
mlx-whisper>=0.4.0
librosa>=0.10.0
pyloudnorm>=0.1.0
editdistance>=0.6.0
soundfile>=0.12.0
```

**IMPORTANT:** These libraries may not work with Python 3.14. If import errors occur:
- Try `pip install audioop-lts` for pydub/audioop compatibility (already installed)
- For librosa: if numpy issues, try `pip install numpy<2.0`
- For mlx-whisper: should work since mlx already installed for TTS

### Module structure:
```
src/pipeline/audio_qa/
    __init__.py
    transcription_checker.py    # Task 2: Whisper transcription + WER
    timing_analyzer.py          # Task 3: Pause/pacing analysis
    audio_quality_analyzer.py   # Task 4: LUFS, SNR, artifacts
    qa_scorer.py                # Task 5: Scoring + auto-approve logic
    models.py                   # Pydantic models for QA results
```

---

## Task 2: Transcription Accuracy Checker

**File:** `src/pipeline/audio_qa/transcription_checker.py`

### Requirements:

```python
class TranscriptionChecker:
    """Compares TTS audio against source text using speech-to-text."""

    def __init__(self, whisper_model: str = "base"):
        """
        Initialize with whisper model size.
        Options: "tiny", "base", "small", "medium", "large-v3"
        Use "base" for speed, "medium" for accuracy, "large-v3" for production QA.
        """

    def check_chapter(self, audio_path: str, source_text: str) -> TranscriptionResult:
        """
        Transcribe audio and compare against source text.

        Returns TranscriptionResult with:
        - transcript: str — full transcription
        - wer: float — Word Error Rate (0.0 = perfect, 1.0 = all wrong)
        - word_accuracy: float — 1.0 - wer (percentage of correct words)
        - mismatches: list[TextMismatch] — specific differences found
            - Each mismatch: {position, expected_words, actual_words, type}
            - Types: "substitution", "insertion", "deletion"
        - character_name_issues: list[str] — proper nouns that differ between source and transcript
        """
```

### Implementation notes:
- Use `mlx_whisper.transcribe(audio_path, language="en")` for transcription
- For the audio language, detect from the book's metadata (most will be English)
- Normalize both texts before comparison: lowercase, strip punctuation, collapse whitespace
- Use `editdistance` for WER calculation at word level
- Use `difflib.SequenceMatcher` to identify specific mismatch locations
- For character name detection: find capitalized multi-word sequences in source that don't appear in transcript
- Cache transcriptions to avoid re-running expensive Whisper on the same audio

### WER interpretation:
- < 0.05 (5%): Excellent — auto-approve
- 0.05-0.10 (5-10%): Good — minor issues, likely abbreviations or stylistic differences
- 0.10-0.20 (10-20%): Fair — needs review, possible mispronunciations
- > 0.20 (20%): Poor — significant issues, likely hallucination or wrong content

### Tests:
- Test with known audio+text pair → verify WER is reasonable
- Test with empty audio → verify graceful failure
- Test with text containing character names → verify name detection
- Test normalization handles edge cases (numbers, abbreviations, punctuation)
- Mock Whisper for fast unit tests

---

## Task 3: Timing and Pacing Analyzer

**File:** `src/pipeline/audio_qa/timing_analyzer.py`

### Requirements:

```python
class TimingAnalyzer:
    """Analyzes audio pacing, pauses, and timing characteristics."""

    def analyze(self, audio_path: str, source_text: str) -> TimingResult:
        """
        Returns TimingResult with:
        - duration_seconds: float
        - expected_duration_seconds: float — based on word count × target WPM
        - duration_ratio: float — actual/expected (1.0 = perfect, >1.5 or <0.7 = flag)
        - words_per_minute: float — estimated from word count / duration
        - pause_analysis: PauseAnalysis
            - total_pauses: int
            - avg_pause_duration_ms: float
            - max_pause_duration_ms: float
            - awkward_pauses: list[AwkwardPause] — pauses > 3s that aren't at paragraph/chapter breaks
            - rushed_sections: list[RushedSection] — sections with WPM > 200 (too fast for narration)
        - speech_ratio: float — percentage of audio that contains speech (vs silence)
        """
```

### Implementation notes:
- Use `librosa.load()` to read audio
- Detect speech/silence segments using `librosa.effects.split()` with `top_db=30`
- Calculate WPM: `word_count / (duration_seconds / 60)`
- Target WPM for audiobook narration: 150-170 WPM (125-180 acceptable range)
- Expected duration: `word_count / 155 * 60` seconds (155 WPM average)
- Flag pauses > 3 seconds that aren't at detected paragraph breaks
- Flag sections where local WPM exceeds 200 (rushed) or drops below 100 (dragging)

### Tests:
- Test with normal speech audio → verify WPM in 130-180 range
- Test duration ratio calculation
- Test pause detection with known silence gaps
- Mock librosa for fast unit tests

---

## Task 4: Audio Quality Analyzer

**File:** `src/pipeline/audio_qa/audio_quality_analyzer.py`

### Requirements:

```python
class AudioQualityAnalyzer:
    """Analyzes technical audio quality: loudness, artifacts, consistency."""

    def analyze(self, audio_path: str) -> AudioQualityResult:
        """
        Returns AudioQualityResult with:
        - lufs: float — integrated loudness (target: -19 LUFS for audiobook)
        - lufs_range: float — loudness range (LRA) in LU
        - peak_dbfs: float — true peak
        - dynamic_range_db: float — difference between peak and RMS
        - snr_estimate_db: float — estimated signal-to-noise ratio
        - clipping_detected: bool — any samples at max amplitude
        - clipping_percentage: float — percentage of samples that clip
        - artifact_count: int — detected clicks/pops via spectral analysis
        - artifact_timestamps: list[float] — seconds where artifacts detected
        - sample_rate: int
        - channels: int
        - bit_depth: int
        """
```

### Implementation notes:
- Use `pyloudnorm.Meter(sr).integrated_loudness(audio)` for LUFS
- Detect clipping: samples where `abs(value) > 0.99`
- SNR estimation: `10 * log10(mean(signal_energy) / mean(noise_energy))` where noise = silent segments
- Click/pop detection: compute spectral flux, flag frames where flux > 3 standard deviations above mean
- For spectral flux: `sum(max(0, S[t] - S[t-1])^2)` where S is magnitude spectrogram

### LUFS targets (audiobook standards):
- ACX/Audible requirement: -23 to -18 LUFS
- Our target: -19 LUFS (±1 LU tolerance)
- True peak: must not exceed -1.0 dBFS

### Tests:
- Test LUFS measurement against known reference
- Test clipping detection with artificially clipped audio
- Test artifact detection with known click injection
- Mock audio loading for fast unit tests

---

## Task 5: QA Scorer and Integration

**File:** `src/pipeline/audio_qa/qa_scorer.py`

### Requirements:

```python
class AudioQAScorer:
    """Combines all QA checks into an overall quality score."""

    def score_chapter(self, audio_path: str, source_text: str) -> ChapterQAReport:
        """
        Run all QA checks and produce a combined score.

        Returns ChapterQAReport with:
        - overall_score: int (0-100)
        - grade: str ("A", "B", "C", "D", "F")
        - auto_approved: bool — True if score >= 80 and no critical issues
        - transcription: TranscriptionResult
        - timing: TimingResult
        - audio_quality: AudioQualityResult
        - issues: list[QAIssue] — all flagged problems
            - Each: {severity: "critical"|"warning"|"info", category, description, timestamp}
        - recommendations: list[str] — suggested fixes
        """

    def score_book(self, book_id: int, db: Session) -> BookQAReport:
        """Score all chapters for a book, produce aggregate report."""
```

### Scoring weights:
- Word accuracy (WER): 35 points (35% of score)
  - WER < 0.03 → 35 points
  - WER 0.03-0.05 → 30 points
  - WER 0.05-0.10 → 20 points
  - WER 0.10-0.20 → 10 points
  - WER > 0.20 → 0 points

- Timing/pacing: 20 points
  - WPM 140-170 → 20 points
  - WPM 125-140 or 170-190 → 15 points
  - WPM 100-125 or 190-210 → 10 points
  - WPM outside 100-210 → 0 points
  - Deduct 2 points per awkward pause

- Audio quality: 25 points
  - LUFS within ±1 of -19 → 25 points
  - LUFS within ±2 → 20 points
  - LUFS within ±3 → 15 points
  - No clipping → +0 (deduct 10 if clipping detected)
  - No artifacts → +0 (deduct 2 per artifact)

- Consistency: 20 points
  - Volume consistent across chapters (LUFS std < 1 LU) → 20 points
  - Duration ratio 0.8-1.2 → 20 points
  - Deduct proportionally for outliers

### Grade thresholds:
- A: 90-100 → Auto-approve, production ready
- B: 80-89 → Auto-approve with minor notes
- C: 70-79 → Manual review recommended
- D: 60-69 → Regeneration recommended
- F: 0-59 → Must regenerate

### API integration:
- Add endpoint: `POST /api/books/{book_id}/chapters/{chapter_id}/deep-qa` — runs full audio QA
- Add endpoint: `POST /api/books/{book_id}/deep-qa` — runs QA on all chapters
- Add endpoint: `GET /api/books/{book_id}/qa-report` — returns book-level QA report
- Store QA results in a new `audio_qa_results` DB table
- Add QA score to existing chapter status display

### Frontend:
- Add "Deep QA" button on chapter detail page (runs single-chapter QA)
- Add "Run Audio QA" button on book detail page (runs all chapters)
- Display QA report with color-coded scores (green A/B, yellow C, red D/F)
- Show specific issues with timestamps for easy manual review
- Show word-level diff between source text and transcription (highlight mismatches in red)

### Tests:
- Test scoring with known good audio → verify A grade
- Test scoring with known problematic audio → verify appropriate flag
- Test auto-approve logic (score >= 80 + no critical issues = approved)
- Test book-level aggregation
- Test API endpoints return correct data
- Minimum 15 new tests

---

## Whisper Model Selection

The system should support configurable model sizes via environment variable `AUDIO_QA_WHISPER_MODEL`:
- `tiny` — fastest, lowest accuracy (good for smoke testing)
- `base` — good balance for development (DEFAULT)
- `small` — better accuracy, still reasonably fast
- `medium` — high accuracy, slower
- `large-v3` — best accuracy, use for final production QA pass

On Apple Silicon M2 Max with 32GB RAM, `medium` should process in near-real-time. `large-v3` will be ~2x slower than real-time but most accurate.

---

## Testing Requirements

- All existing tests must continue to pass (366+ tests)
- Add minimum 20 new tests
- Mock Whisper and librosa for unit tests (don't require actual audio processing in CI)
- Add one integration test that generates a short TTS clip and runs full QA pipeline on it

---

## Files to Create/Modify

| File | Changes |
|------|---------|
| `src/pipeline/audio_qa/__init__.py` | New module |
| `src/pipeline/audio_qa/transcription_checker.py` | New — Whisper transcription + WER |
| `src/pipeline/audio_qa/timing_analyzer.py` | New — Pacing/pause analysis |
| `src/pipeline/audio_qa/audio_quality_analyzer.py` | New — LUFS, SNR, artifacts |
| `src/pipeline/audio_qa/qa_scorer.py` | New — Combined scoring + grading |
| `src/pipeline/audio_qa/models.py` | New — Pydantic models |
| `requirements.txt` | Add mlx-whisper, librosa, pyloudnorm, editdistance, soundfile |
| `src/api/routes/book_routes.py` | Add deep-qa endpoints |
| `src/models/database.py` | Add audio_qa_results table |
| `src/frontend/` | QA report display components |
| `tests/test_audio_qa.py` | New test file |

---

## Acceptance Criteria

1. Running deep QA on a chapter produces a score 0-100 with grade A-F
2. Transcription comparison catches word-level mismatches with WER metric
3. Timing analysis detects awkward pauses and rushed sections
4. LUFS measurement validates against -19 LUFS audiobook target
5. Click/pop detection identifies spectral anomalies
6. Auto-approve works for chapters scoring >= 80 with no critical issues
7. API endpoints return QA reports
8. Frontend shows QA results with color-coded grades
9. All 386+ tests pass
