# PROMPT-25: Quality Gate 2 — Per-Chapter Validation (Consistency, Spectral, Silence)

## Context
This is Gate 2 of the three-gate quality pipeline. It runs **after all chunks for a chapter are stitched** into the final chapter audio. It catches problems that only appear at the chapter level: cross-chunk inconsistencies, spectral artifacts, context-aware silence issues, and overall pacing.

Gate 1 (PROMPT-24) catches per-chunk issues. Gate 2 catches issues that emerge from combining chunks. Gate 3 (PROMPT-26) catches cross-chapter issues.

The existing `qa_checker.py` already has 8 checks (duration, clipping, silence gaps, volume consistency, stitch clicks, pacing, LUFS). This prompt adds the missing checks and improves existing ones.

---

## Task 1: Cross-Chunk Voice Consistency Check

### Problem
Over a long chapter (20+ chunks), the voice character can subtly drift — pitch shifts, timbre changes, or nasality increases. Each chunk passes individually but the chapter sounds inconsistent.

### Implementation

Add to `src/pipeline/qa_checker.py`:

**`check_voice_consistency(audio_path: str, chunk_boundaries: list[float]) -> QACheckResult`**

1. Split the chapter audio at known chunk boundaries (timestamps where chunks were stitched)
2. For each chunk region, compute:
   - **Mean fundamental frequency (F0)**: Using autocorrelation pitch detection
     ```python
     def estimate_pitch(audio_segment, sample_rate):
         """Estimate median F0 using autocorrelation."""
         import numpy as np
         signal = np.array(audio_segment.get_array_of_samples(), dtype=np.float64)
         # Autocorrelation method
         # Look for peaks in lag range 50-500 (corresponding to 48-480 Hz)
         # Return median pitch across 100ms frames
     ```
   - **Spectral centroid**: Center of mass of the frequency spectrum (brightness indicator)
   - **RMS energy**: Already computed elsewhere but needed for this comparison
3. Compare each chunk's metrics against the chapter median:
   - Pitch deviation > 15% from median: `WARNING`
   - Spectral centroid deviation > 20% from median: `WARNING`
   - Any single metric with > 25% deviation: `FAIL`
4. Report: which chunk indices deviate and by how much

### Chunk Boundary Tracking
The generator must record chunk stitch timestamps. Add to the chapter database record or a JSON sidecar file:
```python
# In generator.py, after stitching:
chapter.chunk_boundaries = json.dumps(boundary_timestamps)  # [0.0, 4.5, 9.2, 14.8, ...]
```

Add a `chunk_boundaries` text column to the chapter model in `src/database.py` if it doesn't exist.

---

## Task 2: Spectral Artifact Detection

### Problem
TTS models can produce artifacts invisible in time-domain analysis: background hum (50/60Hz electrical noise simulation), high-frequency ringing, metallic resonance, or broadband noise bursts. These are subtle but fatiguing over hours of listening.

### Implementation

Add to `src/pipeline/qa_checker.py`:

**`check_spectral_quality(audio_path: str) -> QACheckResult`**

1. **Low-frequency hum detection**:
   ```python
   def detect_hum(samples, sample_rate):
       """Check for energy concentration at 50Hz, 60Hz, and harmonics."""
       import numpy as np
       # FFT of the entire signal
       fft = np.fft.rfft(samples)
       freqs = np.fft.rfftfreq(len(samples), 1/sample_rate)
       magnitude = np.abs(fft)

       # Check 50Hz, 60Hz and their harmonics (100, 120, 150, 180 Hz)
       hum_freqs = [50, 60, 100, 120, 150, 180]
       for target in hum_freqs:
           # Find energy in ±2Hz band around target
           band_mask = (freqs >= target - 2) & (freqs <= target + 2)
           band_energy = np.mean(magnitude[band_mask])
           # Compare to surrounding 20Hz band
           surround_mask = (freqs >= target - 10) & (freqs <= target + 10) & ~band_mask
           surround_energy = np.mean(magnitude[surround_mask])
           if band_energy > surround_energy * 5:  # 5x concentration = hum
               return True, target
       return False, None
   ```
   - Hum detected: `WARNING` with frequency identified

2. **High-frequency artifact detection**:
   - Compute energy above 8kHz relative to total energy
   - TTS at 24kHz sample rate shouldn't have significant energy above 10kHz
   - If >15% of total energy is above 8kHz: `WARNING` (possible ringing/aliasing)

3. **Noise floor analysis**:
   - During detected silence regions, measure noise floor
   - Clean TTS silence should be < -60dBFS
   - If silence regions have noise > -45dBFS: `WARNING`

### Thresholds
```python
hum_detection_enabled: bool = True
hum_concentration_ratio: float = 5.0  # target band vs surrounding
high_freq_energy_warning: float = 0.15  # 15% of energy above 8kHz
noise_floor_warning_db: float = -45.0
```

---

## Task 3: Context-Aware Silence Detection

### Problem
The current `check_silence_gaps()` uses a fixed 3-5 second threshold. But context matters:
- 1.5s silence mid-sentence = BAD (dropout)
- 1.5s silence between paragraphs = GOOD (natural pause)
- 0.3s silence between sentences = TOO SHORT (rushed)

### Implementation

Replace `check_silence_gaps()` with:

**`check_contextual_silence(audio_path: str, text_content: str, chunk_boundaries: list[float]) -> QACheckResult`**

1. Detect all silence regions > 200ms in the chapter audio
2. For each silence region, determine its textual context:
   - Map the silence timestamp to the corresponding text position using chunk boundaries and proportional mapping
   - Classify: mid-sentence, sentence boundary, paragraph boundary, chapter start/end
3. Apply context-specific thresholds:
   ```python
   CONTEXT_SILENCE_RULES = {
       "mid_sentence": {"min_ms": 0, "max_ms": 800, "expected_ms": 200},
       "sentence_boundary": {"min_ms": 300, "max_ms": 1500, "expected_ms": 600},
       "paragraph_boundary": {"min_ms": 600, "max_ms": 2500, "expected_ms": 1200},
       "dialogue_transition": {"min_ms": 400, "max_ms": 1200, "expected_ms": 700},
       "chapter_start": {"min_ms": 500, "max_ms": 2000, "expected_ms": 1000},
       "chapter_end": {"min_ms": 500, "max_ms": 3000, "expected_ms": 1500},
   }
   ```
4. Any silence outside its context range: `WARNING`
5. Silence > 5s anywhere: `FAIL` (definite dropout)
6. Report includes: timestamp, duration, context, expected range

---

## Task 4: Improved Stitch Quality Check

### Problem
The current `check_stitch_clicks()` uses a fixed 5ms window and 12dB threshold. This misses tonal discontinuities (not clicks, but noticeable timbre shifts at stitch points).

### Implementation

Enhance `check_stitch_clicks()`:

**`check_stitch_quality(audio_path: str, chunk_boundaries: list[float]) -> QACheckResult`**

1. **Keep existing click detection** (5ms window, 12dB spike) — it works
2. **Add tonal discontinuity detection**:
   - At each stitch boundary, extract 50ms of audio before and after
   - Compute spectral centroid of each 50ms segment
   - If spectral centroid shifts > 30%: `WARNING` (tonal jump)
3. **Add energy discontinuity detection**:
   - Compare RMS of 100ms before vs 100ms after each stitch
   - If RMS jumps > 6dB: `WARNING` (volume jump at stitch)
4. Report: which stitch points have issues and what type

---

## Task 5: Pacing Consistency Improvement

### Problem
The current `check_pacing_consistency()` uses 40% deviation threshold — too lenient. Audiobook listeners notice 15-20% speech rate changes.

### Implementation

Replace pacing check with:

**`check_pacing_detailed(audio_path: str, text_content: str) -> QACheckResult`**

1. Compute speech rate in 10-second windows across the chapter:
   - Use voice activity detection (VAD) to measure actual speaking time per window
   - Estimate words per window using proportional text mapping
   - Calculate words-per-minute (WPM) for each window
2. Compute chapter-wide statistics: mean WPM, std dev, min, max
3. Thresholds:
   - Individual window > 25% from mean: `WARNING`
   - Individual window > 40% from mean: `FAIL`
   - Standard deviation > 20% of mean: `WARNING` (overall inconsistency)
4. Report includes: WPM statistics and timestamps of outlier windows

---

## Task 6: Adaptive Crossfade Recommendation

### Problem
The fixed 30ms crossfade works for most stitches but can produce artifacts when chunk boundaries have very different tonal characteristics.

### Implementation

Add to `src/engines/chunker.py` in `AudioStitcher`:

**`compute_adaptive_crossfade(chunk_a: AudioSegment, chunk_b: AudioSegment) -> int`**

1. Extract last 100ms of chunk_a and first 100ms of chunk_b
2. Compute spectral similarity (cosine similarity of magnitude spectra)
3. Map similarity to crossfade duration:
   - Very similar (>0.9): 20ms crossfade (quick, clean)
   - Moderate (0.7-0.9): 50ms crossfade (standard)
   - Different (<0.7): 100ms crossfade (smooth the transition)
   - Very different (<0.5): 150ms crossfade + log WARNING
4. Use this instead of the fixed `CROSSFADE_MS = 30`

Wire into `AudioStitcher.stitch()` as the default behavior (keep fixed crossfade as a fallback option).

---

## Task 7: Chapter QA Summary Report

### Implementation

After all chapter-level checks run, generate a structured summary:

```python
@dataclass
class ChapterQAReport:
    chapter_number: int
    chapter_title: str
    duration_seconds: float
    total_checks: int
    passed: int
    warnings: int
    failures: int
    results: list[QACheckResult]
    pacing_stats: dict  # {mean_wpm, std_wpm, min_wpm, max_wpm}
    silence_stats: dict  # {count, min_ms, max_ms, avg_ms}
    stitch_quality: dict  # {total_stitches, clean, warnings, failures}

    @property
    def overall_grade(self) -> str:
        """A/B/C/F grading."""
        if self.failures > 0:
            return "F"
        if self.warnings > 3:
            return "C"
        if self.warnings > 0:
            return "B"
        return "A"

    @property
    def ready_for_export(self) -> bool:
        return self.overall_grade in ("A", "B")
```

Store this report as JSON alongside the chapter audio file. Display the grade in the frontend QA dashboard.

---

## Task 8: Tests

Create `tests/test_chapter_quality_gate.py`:

1. `test_voice_consistency_stable` — chapter with consistent voice passes
2. `test_voice_consistency_drift` — chapter with pitch drift flagged
3. `test_spectral_no_hum` — clean audio passes spectral check
4. `test_spectral_hum_detected` — audio with 60Hz tone flagged
5. `test_contextual_silence_paragraph` — 1.2s silence at paragraph boundary = PASS
6. `test_contextual_silence_mid_sentence` — 1.2s silence mid-sentence = WARNING
7. `test_stitch_tonal_discontinuity` — spectral centroid jump at stitch = WARNING
8. `test_pacing_consistent` — steady WPM passes
9. `test_pacing_inconsistent` — wildly varying WPM flagged
10. `test_adaptive_crossfade_similar` — similar chunks get short crossfade
11. `test_adaptive_crossfade_different` — different chunks get long crossfade
12. `test_chapter_qa_grade` — report grades correctly computed

All existing tests must still pass.

---

## Priority Order
1. Task 7 (ChapterQAReport model — structures all results)
2. Task 3 (Context-aware silence — fixes biggest false positive/negative issue)
3. Task 5 (Pacing consistency — tightens existing check)
4. Task 1 (Voice consistency — catches drift)
5. Task 4 (Stitch quality — improves existing check)
6. Task 2 (Spectral artifacts — catches subtle issues)
7. Task 6 (Adaptive crossfade — quality improvement)
8. Task 8 (Tests)
