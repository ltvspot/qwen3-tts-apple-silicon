# PROMPT-26: Quality Gate 3 — Per-Book Validation (Cross-Chapter Consistency & Final Mastering)

## Context
This is Gate 3 of the three-gate quality pipeline. It runs **after all chapters are generated and individually QA'd**, before the final audiobook export. It catches problems that only appear when comparing chapters against each other: loudness inconsistency, voice drift across the book, pacing variations, and ensures the final product meets professional audiobook standards (ACX/Audible).

Gate 1 (per-chunk) catches generation errors. Gate 2 (per-chapter) catches stitching and chapter-level issues. Gate 3 catches the whole-book issues that make the difference between "good enough" and "5-star reviews every time."

---

## Task 1: Cross-Chapter Loudness Normalization

### Problem
Each chapter is individually normalized to -18 dBFS with LUFS checked per-chapter. But chapter-to-chapter loudness can still vary by 2-3 dB because the content density differs (dialogue-heavy chapters are quieter than narration-heavy ones). Over a 10-hour audiobook, listeners constantly adjust volume.

### Implementation

Create `src/pipeline/book_qa.py`:

**`check_cross_chapter_loudness(book_id: int, db_session: Session) -> BookQAResult`**

1. For each completed chapter, measure integrated LUFS (already done per-chapter, retrieve from DB or re-measure)
2. Compute book-wide statistics: mean LUFS, std deviation, min/max
3. Thresholds:
   - All chapters within ±1.5 LU of mean: `PASS`
   - Any chapter deviating 1.5–3.0 LU: `WARNING` — suggest re-normalization
   - Any chapter deviating > 3.0 LU: `FAIL` — must re-normalize before export
4. **Auto-fix**: When exporting, apply per-chapter gain adjustment to bring all chapters within ±0.5 LU of the target (-20 LUFS for ACX compliance)

### Exporter Integration
Modify `src/pipeline/exporter.py`:
- Before concatenating chapters, compute the LUFS delta for each chapter
- Apply gain offset: `chapter_audio = chapter_audio + gain_offset_db`
- This is a simple amplitude adjustment, not re-normalization — preserves dynamics

---

## Task 2: Cross-Chapter Voice Consistency

### Problem
Over a 20-chapter book generated across hours (or days with model reloads), the voice character can shift. Chapter 1 might sound warm and resonant, chapter 15 might sound thinner or faster after a model reload.

### Implementation

**`check_cross_chapter_voice(book_id: int, db_session: Session) -> BookQAResult`**

1. For each chapter, compute a "voice fingerprint" — a vector of acoustic features:
   ```python
   def compute_voice_fingerprint(audio_path: str) -> dict:
       """Extract acoustic features that characterize the voice."""
       # Load audio, take 30-second sample from middle of chapter
       # (avoid intro/outro silence)
       return {
           "mean_pitch_hz": float,       # Median F0
           "pitch_range_hz": float,      # F0 standard deviation
           "spectral_centroid": float,    # Brightness
           "speech_rate_wpm": float,      # Words per minute estimate
           "mean_rms_db": float,          # Average energy
           "spectral_bandwidth": float,   # Voice "width"
       }
   ```
2. Compare each chapter's fingerprint to the book median:
   - Pitch deviation > 10%: `WARNING`
   - Speech rate deviation > 12%: `WARNING`
   - Spectral centroid deviation > 15%: `WARNING`
   - Any metric > 20% deviation: `FAIL`
3. Report includes: which chapters are outliers, which metrics deviate, and by how much

### Visualization
Return data suitable for the frontend to display a "voice consistency chart":
```json
{
  "chapters": [
    {"number": 1, "pitch": 142.3, "rate": 155, "brightness": 2340, "grade": "A"},
    {"number": 2, "pitch": 145.1, "rate": 158, "brightness": 2380, "grade": "A"},
    {"number": 3, "pitch": 138.0, "rate": 148, "brightness": 2290, "grade": "B"},
    ...
  ],
  "book_median": {"pitch": 143.5, "rate": 154, "brightness": 2350},
  "outlier_chapters": [3, 15]
}
```

---

## Task 3: Cross-Chapter Pacing Consistency

### Problem
Speech rate naturally varies by content (action scenes faster, reflective passages slower), but the overall pacing should feel consistent. A chapter that's 20% faster than the rest breaks immersion.

### Implementation

**`check_cross_chapter_pacing(book_id: int, db_session: Session) -> BookQAResult`**

1. For each chapter, compute estimated WPM:
   - `chapter_wpm = word_count / (audio_duration_seconds / 60)`
   - Exclude leading/trailing silence from duration
2. Book-wide statistics: mean WPM, std dev, range
3. Thresholds:
   - All chapters within ±10% of mean: `PASS`
   - Any chapter 10-20% off: `WARNING`
   - Any chapter > 20% off: `FAIL` — suggest re-generation at adjusted speed
4. **Auto-fix suggestion**: For outlier chapters, calculate the speed adjustment needed:
   ```python
   target_wpm = book_mean_wpm
   current_wpm = chapter_wpm
   suggested_speed = current_wpm / target_wpm  # e.g., 1.12 if chapter is 12% slow
   ```

---

## Task 4: Chapter Transition Quality

### Problem
When chapters are concatenated in the final audiobook, the transition between the end of one chapter and the start of the next must feel natural. Abrupt loudness changes, tonal shifts, or insufficient silence at chapter boundaries are jarring.

### Implementation

**`check_chapter_transitions(book_id: int, db_session: Session) -> BookQAResult`**

1. For each consecutive chapter pair (N, N+1):
   - Load the last 3 seconds of chapter N and first 3 seconds of chapter N+1
   - Compare:
     - RMS energy difference: should be < 3 dB
     - Spectral centroid difference: should be < 20%
     - Both should end/start with appropriate silence (500ms+ trailing, 500ms+ leading)
2. Thresholds:
   - Energy jump > 3 dB: `WARNING`
   - Energy jump > 6 dB: `FAIL`
   - No trailing silence on chapter N: `WARNING`
   - No leading silence on chapter N+1: `WARNING`

---

## Task 5: ACX/Audible Compliance Check

### Problem
ACX (Audible's production standard) has specific requirements. Non-compliance means rejection.

### Implementation

**`check_acx_compliance(book_id: int, db_session: Session) -> BookQAResult`**

ACX requirements:
```python
ACX_REQUIREMENTS = {
    "sample_rate": 44100,          # Must be 44.1kHz
    "bit_depth": 16,               # 16-bit
    "channels": 1,                 # Mono
    "lufs_min": -23,               # Integrated loudness floor
    "lufs_max": -18,               # Integrated loudness ceiling
    "peak_max_db": -3.0,           # True peak maximum
    "noise_floor_max_db": -60,     # Room noise during silence
    "min_leading_silence_ms": 500,  # Head silence: 0.5-1.0s
    "max_leading_silence_ms": 1000,
    "min_trailing_silence_ms": 1000, # Tail silence: 1.0-5.0s
    "max_trailing_silence_ms": 5000,
    "min_chapter_duration_s": 1,    # No empty chapters
    "max_file_size_mb": 170,        # Per-file limit for ACX
}
```

Check each requirement for every chapter:
1. Sample rate (after export resampling)
2. Bit depth
3. Channel count
4. Integrated LUFS
5. True peak (not just sample peak — use oversampled peak detection)
6. Noise floor during detected silence regions
7. Leading/trailing silence duration
8. File size

Any violation: `FAIL` with specific remediation instructions.

---

## Task 6: Book QA Dashboard API

### Implementation

Add endpoints to serve book-level QA data:

**`GET /api/book/{book_id}/qa/book-report`**

Returns:
```json
{
  "book_id": 42,
  "title": "The Great Gatsby",
  "total_chapters": 9,
  "chapters_grade_a": 7,
  "chapters_grade_b": 1,
  "chapters_grade_c": 1,
  "chapters_grade_f": 0,
  "overall_grade": "B",
  "ready_for_export": true,
  "cross_chapter_checks": {
    "loudness_consistency": {"status": "pass", "mean_lufs": -19.2, "max_deviation_lu": 1.1},
    "voice_consistency": {"status": "warning", "outlier_chapters": [3]},
    "pacing_consistency": {"status": "pass", "mean_wpm": 154, "max_deviation_pct": 8.2},
    "chapter_transitions": {"status": "pass", "issues": []},
    "acx_compliance": {"status": "pass", "violations": []}
  },
  "recommendations": [
    "Chapter 3 has 12% pitch deviation — consider regenerating",
    "All chapters within ACX loudness range"
  ],
  "export_blockers": []  // Empty = safe to export
}
```

**`GET /api/book/{book_id}/qa/voice-consistency-chart`**

Returns the per-chapter voice fingerprint data for frontend visualization.

---

## Task 7: Auto-Fix Pipeline

### Problem
When Gate 3 finds issues, we need automated fixes — not just reports.

### Implementation

Create `src/pipeline/book_mastering.py`:

**`class BookMasteringPipeline`**

```python
class BookMasteringPipeline:
    """Post-generation mastering to ensure consistent, professional quality."""

    async def master_book(self, book_id: int, db_session: Session) -> MasteringReport:
        """Run all auto-fixes before export."""

        # Step 1: Cross-chapter loudness leveling
        await self._normalize_loudness(book_id, db_session)

        # Step 2: Chapter edge silence normalization
        await self._normalize_chapter_edges(book_id, db_session)

        # Step 3: Apply sentence-boundary padding (from PROMPT-23)
        # (Already done during generation, but verify here)

        # Step 4: Final peak limiting
        await self._apply_final_peak_limit(book_id, db_session)

        # Step 5: Re-run Gate 2 checks on mastered audio
        await self._verify_mastered_quality(book_id, db_session)

        return report

    async def _normalize_loudness(self, book_id, db_session):
        """Adjust per-chapter gain to achieve consistent LUFS across book."""
        target_lufs = -20.0  # ACX sweet spot
        for chapter in chapters:
            current_lufs = measure_lufs(chapter.audio_path)
            gain_db = target_lufs - current_lufs
            if abs(gain_db) > 0.5:  # Only adjust if deviation is meaningful
                apply_gain(chapter.audio_path, gain_db)
                chapter.mastered = True

    async def _normalize_chapter_edges(self, book_id, db_session):
        """Ensure consistent lead-in/trail-out silence on every chapter."""
        TARGET_LEAD_IN_MS = 750    # 0.75 seconds
        TARGET_TRAIL_OUT_MS = 1500  # 1.5 seconds

        for chapter in chapters:
            audio = AudioSegment.from_wav(chapter.audio_path)

            # Trim existing silence, then add exact target
            trimmed = strip_silence(audio, silence_thresh=-45)
            padded = (
                AudioSegment.silent(duration=TARGET_LEAD_IN_MS, frame_rate=audio.frame_rate)
                + trimmed
                + AudioSegment.silent(duration=TARGET_TRAIL_OUT_MS, frame_rate=audio.frame_rate)
            )
            padded.export(chapter.audio_path, format="wav")

    async def _apply_final_peak_limit(self, book_id, db_session):
        """Ensure no chapter exceeds -3dB true peak (ACX requirement)."""
        for chapter in chapters:
            audio = AudioSegment.from_wav(chapter.audio_path)
            peak_db = audio.max_dBFS
            if peak_db > -3.0:
                reduction = peak_db - (-3.5)  # Target -3.5 for safety margin
                audio = audio - reduction
                audio.export(chapter.audio_path, format="wav")
```

### Wire into Export Pipeline
In `src/pipeline/exporter.py`, before concatenating chapters:
```python
# Run mastering pipeline before export
mastering = BookMasteringPipeline()
report = await mastering.master_book(book_id, db_session)
if report.has_blockers:
    raise ExportBlockedError(f"Mastering found blocking issues: {report.blockers}")
```

---

## Task 8: Frontend — Book QA Overview

Add a "Book Quality" tab or section to the BookDetail page:

1. **Overall grade badge**: A/B/C/F with color coding (green/yellow/orange/red)
2. **Cross-chapter checks**: List with pass/warn/fail icons
3. **Voice consistency chart**: Simple bar chart showing per-chapter pitch/rate/brightness relative to median
4. **Export readiness indicator**: Green "Ready for Export" or red "Issues must be resolved" with actionable list
5. **"Run Book QA" button**: Triggers Gate 3 checks on demand
6. **"Auto-Master" button**: Runs the mastering pipeline, then re-checks

---

## Task 9: Tests

Create `tests/test_book_quality_gate.py`:

1. `test_loudness_consistency_pass` — chapters within ±1.5 LU pass
2. `test_loudness_consistency_fail` — chapter 3 LU off fails
3. `test_voice_consistency_stable` — consistent fingerprints pass
4. `test_voice_consistency_drift` — pitch drift flagged
5. `test_pacing_consistency_even` — similar WPM pass
6. `test_pacing_consistency_outlier` — 25% faster chapter fails
7. `test_chapter_transition_smooth` — matching energy passes
8. `test_chapter_transition_jarring` — 8dB jump fails
9. `test_acx_compliance_pass` — properly formatted audio passes all ACX checks
10. `test_acx_compliance_peak_violation` — audio with peaks > -3dB fails
11. `test_mastering_normalizes_loudness` — mastering brings chapters within ±0.5 LU
12. `test_mastering_normalizes_edges` — chapters get exact lead-in/trail-out silence
13. `test_book_qa_api_endpoint` — /api/book/{id}/qa/book-report returns correct structure

Create `tests/test_book_mastering.py`:

1. `test_master_book_adjusts_gain` — quiet chapter gets boosted
2. `test_master_book_trims_silence` — excess silence trimmed to target
3. `test_master_book_peak_limits` — hot peaks reduced to -3.5dB
4. `test_master_book_preserves_good_audio` — chapters already within spec unchanged

All existing tests must still pass.

Rebuild frontend after all changes: `cd frontend && npm run build`

---

## Priority Order
1. Task 5 (ACX compliance — hard requirements, non-negotiable)
2. Task 1 (Cross-chapter loudness — most audible issue)
3. Task 7 (Auto-fix mastering pipeline — automates the fixes)
4. Task 3 (Pacing consistency — listener experience)
5. Task 2 (Voice consistency — catches drift)
6. Task 4 (Chapter transitions — polish)
7. Task 6 (API endpoints)
8. Task 8 (Frontend UI)
9. Task 9 (Tests)

---

## Summary: Three-Gate Quality Pipeline

After PROMPT-24, 25, and 26, the full pipeline is:

```
TEXT → CHUNK → [GATE 1: validate chunk] → STITCH → [GATE 2: validate chapter] → ALL CHAPTERS → [GATE 3: validate book] → MASTER → EXPORT

Gate 1 (per-chunk):
  ✓ Duration validation (enhanced)
  ✓ Silence floor
  ✓ Clipping detection
  ✓ Sample rate check
  ✓ STT text alignment
  ✓ Repeat/loop detection
  ✓ Gibberish/clarity check
  ✓ Auto-regeneration on FAIL

Gate 2 (per-chapter):
  ✓ Cross-chunk voice consistency
  ✓ Spectral artifact detection (hum, ringing)
  ✓ Context-aware silence validation
  ✓ Enhanced stitch quality
  ✓ Tight pacing consistency
  ✓ Adaptive crossfade
  ✓ Chapter QA grade (A/B/C/F)

Gate 3 (per-book):
  ✓ Cross-chapter loudness normalization
  ✓ Cross-chapter voice consistency
  ✓ Cross-chapter pacing consistency
  ✓ Chapter transition quality
  ✓ ACX/Audible compliance
  ✓ Auto-mastering pipeline
  ✓ Book-level QA grade + export readiness
```

Every audiobook that passes all three gates meets professional standards.
