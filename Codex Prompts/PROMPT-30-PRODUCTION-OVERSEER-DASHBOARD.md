# PROMPT-30: Production Overseer Dashboard & Quality Reporting

## Context
This is the final prompt in the production hardening series. It creates the "Production Overseer" — a comprehensive dashboard and API layer that gives full visibility into the quality of every audiobook. This is what allows a human or AI overseer to verify that every audiobook is 100% perfect before export.

---

## Task 1: Pre-Generation Manuscript Validation

### Problem
Bad input produces bad output. If a manuscript has OCR artifacts, truncated chapters, encoding issues, or extremely long paragraphs (which cause chunking problems), these should be caught BEFORE generation starts — not discovered after hours of processing.

### Implementation

Create `src/pipeline/manuscript_validator.py`:

```python
"""Pre-generation validation of manuscript text quality."""

import re
from dataclasses import dataclass, field

@dataclass
class ManuscriptIssue:
    severity: str  # "error", "warning", "info"
    chapter: int | None
    description: str
    suggestion: str

@dataclass
class ManuscriptValidationReport:
    book_id: int
    title: str
    total_chapters: int
    total_words: int
    issues: list[ManuscriptIssue] = field(default_factory=list)
    difficulty_score: float = 0.0  # 0-10, higher = harder for TTS
    ready_for_generation: bool = True

class ManuscriptValidator:

    @classmethod
    def validate(cls, book_id: int, chapters: list[dict]) -> ManuscriptValidationReport:
        """Run all validation checks on manuscript text."""
        report = ManuscriptValidationReport(
            book_id=book_id,
            title=chapters[0].get("book_title", "Unknown") if chapters else "Unknown",
            total_chapters=len(chapters),
            total_words=sum(len(ch.get("text", "").split()) for ch in chapters),
        )

        difficulty_factors = []

        for ch in chapters:
            text = ch.get("text", "")
            ch_num = ch.get("number", 0)

            # 1. Empty or very short chapters
            if len(text.strip()) < 50:
                report.issues.append(ManuscriptIssue(
                    severity="warning", chapter=ch_num,
                    description=f"Chapter {ch_num} is very short ({len(text)} chars)",
                    suggestion="Verify this chapter parsed correctly",
                ))

            # 2. Very long chapters (memory risk)
            word_count = len(text.split())
            if word_count > 20000:
                report.issues.append(ManuscriptIssue(
                    severity="warning", chapter=ch_num,
                    description=f"Chapter {ch_num} is very long ({word_count} words)",
                    suggestion="Generation may use significant memory. Model reloads likely.",
                ))
                difficulty_factors.append(2.0)

            # 3. Encoding artifacts (mojibake)
            mojibake_patterns = [r'â€™', r'â€œ', r'â€', r'Ã©', r'Ã¡', r'Â', r'\ufffd']
            for pattern in mojibake_patterns:
                if re.search(pattern, text):
                    report.issues.append(ManuscriptIssue(
                        severity="error", chapter=ch_num,
                        description=f"Chapter {ch_num} contains encoding artifacts ({pattern})",
                        suggestion="Re-parse the manuscript with correct encoding (UTF-8)",
                    ))
                    report.ready_for_generation = False

            # 4. Proper noun density (pronunciation risk)
            capitalized_words = re.findall(r'\b[A-Z][a-z]{3,}\b', text)
            common_caps = {"The", "And", "But", "For", "Not", "You", "All", "Can", "Her",
                          "Was", "One", "Our", "Out", "His", "Has", "How", "Man", "New",
                          "Now", "Old", "See", "Way", "Who", "Boy", "Did", "Get", "Let"}
            proper_nouns = [w for w in capitalized_words if w not in common_caps]
            noun_density = len(proper_nouns) / max(word_count, 1)
            if noun_density > 0.05:  # More than 5% proper nouns
                report.issues.append(ManuscriptIssue(
                    severity="info", chapter=ch_num,
                    description=f"Chapter {ch_num} has high proper noun density ({noun_density*100:.1f}%)",
                    suggestion="Monitor pronunciation quality closely for this chapter",
                ))
                difficulty_factors.append(1.5)

            # 5. Dialogue ratio (voice switching risk)
            dialogue_chars = len(re.findall(r'["\u201c\u201d][^"\u201c\u201d]{5,}["\u201c\u201d]', text))
            dialogue_ratio = dialogue_chars / max(len(text), 1)
            if dialogue_ratio > 0.6:
                report.issues.append(ManuscriptIssue(
                    severity="info", chapter=ch_num,
                    description=f"Chapter {ch_num} is dialogue-heavy ({dialogue_ratio*100:.0f}%)",
                    suggestion="Single-voice narration may sound less natural for heavy dialogue",
                ))
                difficulty_factors.append(1.0)

            # 6. Non-English words
            non_ascii = re.findall(r'[àáâãäåèéêëìíîïòóôõöùúûüýÿñçæœ]+', text.lower())
            if len(non_ascii) > 5:
                report.issues.append(ManuscriptIssue(
                    severity="warning", chapter=ch_num,
                    description=f"Chapter {ch_num} contains non-English characters ({len(non_ascii)} instances)",
                    suggestion="These may trigger pronunciation artifacts. Review generated audio carefully.",
                ))
                difficulty_factors.append(2.0)

            # 7. Very long paragraphs (chunking risk)
            paragraphs = text.split('\n\n')
            long_paras = [p for p in paragraphs if len(p) > 2000]
            if long_paras:
                report.issues.append(ManuscriptIssue(
                    severity="info", chapter=ch_num,
                    description=f"Chapter {ch_num} has {len(long_paras)} very long paragraphs (>2000 chars)",
                    suggestion="Chunking will split these. Check for natural break points.",
                ))

            # 8. Poetry / verse detection (pacing risk)
            short_lines = [l for l in text.split('\n') if 0 < len(l.strip()) < 60]
            if len(short_lines) > 10 and len(short_lines) / max(len(text.split('\n')), 1) > 0.5:
                report.issues.append(ManuscriptIssue(
                    severity="warning", chapter=ch_num,
                    description=f"Chapter {ch_num} appears to contain poetry/verse",
                    suggestion="TTS may not handle line breaks and meter correctly. Review pacing.",
                ))
                difficulty_factors.append(2.5)

        # Compute overall difficulty score (0-10)
        if difficulty_factors:
            report.difficulty_score = min(10.0, sum(difficulty_factors) / len(chapters) * 2)
        else:
            report.difficulty_score = 1.0

        # Block generation if any errors found
        if any(i.severity == "error" for i in report.issues):
            report.ready_for_generation = False

        return report
```

### API Endpoint
```python
GET /api/book/{book_id}/validate-manuscript

Returns:
{
    "book_id": 42,
    "title": "The Great Gatsby",
    "total_chapters": 9,
    "total_words": 47094,
    "difficulty_score": 3.2,
    "ready_for_generation": true,
    "issues": [
        {"severity": "info", "chapter": 3, "description": "...", "suggestion": "..."},
        ...
    ],
    "issue_summary": {"errors": 0, "warnings": 2, "info": 5}
}
```

---

## Task 2: Quality Trend Tracking

### Problem
Without tracking quality metrics over time, you can't tell if the system is degrading (model drift, config changes, memory issues).

### Implementation

Create `src/pipeline/quality_tracker.py`:

```python
"""Track audio quality metrics across books for trend analysis."""

from dataclasses import dataclass
from datetime import datetime

@dataclass
class BookQualitySnapshot:
    book_id: int
    title: str
    completed_at: datetime
    total_chapters: int
    gate1_pass_rate: float  # % of chunks that passed Gate 1 on first attempt
    gate2_avg_grade: float  # Average chapter grade (A=4, B=3, C=2, F=0)
    gate3_overall_grade: str  # A/B/C/F
    chunks_regenerated: int  # How many chunks needed regeneration
    avg_wer: float  # Average word error rate across chapters
    avg_lufs: float  # Average integrated loudness
    generation_rtf: float  # Real-time factor (generation time / audio time)
    issues_found: int  # Total QA issues across all gates

class QualityTracker:

    @classmethod
    def record_book_quality(cls, book_id: int, db_session) -> BookQualitySnapshot:
        """Compute and store quality metrics for a completed book."""
        # Query all chapter QA results, chunk validation results, etc.
        # Aggregate into BookQualitySnapshot
        # Store in quality_snapshots table
        ...

    @classmethod
    def get_quality_trend(cls, last_n_books: int = 20, db_session=None) -> dict:
        """Get quality metrics trend for the last N books."""
        snapshots = cls._get_recent_snapshots(last_n_books, db_session)
        return {
            "books_analyzed": len(snapshots),
            "avg_gate1_pass_rate": mean([s.gate1_pass_rate for s in snapshots]),
            "avg_gate2_grade": mean([s.gate2_avg_grade for s in snapshots]),
            "gate3_grade_distribution": {
                "A": sum(1 for s in snapshots if s.gate3_overall_grade == "A"),
                "B": sum(1 for s in snapshots if s.gate3_overall_grade == "B"),
                "C": sum(1 for s in snapshots if s.gate3_overall_grade == "C"),
                "F": sum(1 for s in snapshots if s.gate3_overall_grade == "F"),
            },
            "avg_chunks_regenerated": mean([s.chunks_regenerated for s in snapshots]),
            "avg_generation_rtf": mean([s.generation_rtf for s in snapshots]),
            "trend": "stable" if _is_stable(snapshots) else "degrading",
            "alerts": _generate_trend_alerts(snapshots),
        }
```

Add database table:
```python
class QualitySnapshot(Base):
    __tablename__ = "quality_snapshots"
    id = Column(Integer, primary_key=True)
    book_id = Column(Integer, ForeignKey("books.id"))
    completed_at = Column(DateTime)
    gate1_pass_rate = Column(Float)
    gate2_avg_grade = Column(Float)
    gate3_overall_grade = Column(String(1))
    chunks_regenerated = Column(Integer)
    avg_wer = Column(Float, nullable=True)
    avg_lufs = Column(Float)
    generation_rtf = Column(Float)
    issues_found = Column(Integer)
```

---

## Task 3: Production Overseer API

### Implementation

Add comprehensive API endpoints:

```python
# Book-level quality
GET /api/overseer/book/{book_id}/report
# Returns: complete QA report across all three gates

GET /api/overseer/book/{book_id}/flagged-chapters
# Returns: list of chapters with grade C or F, with specific issues

GET /api/overseer/book/{book_id}/export-checklist
# Returns: pre-export verification checklist with pass/fail status:
# - All chapters generated
# - All chapters passed Gate 2 with grade A or B
# - Book passed Gate 3
# - Mastering pipeline completed
# - ACX compliance verified
# - Metadata complete (title, author, narrator, chapter names)

# Quality trends
GET /api/overseer/quality-trend?last_n=20
# Returns: quality metrics over last 20 books

GET /api/overseer/quality-trend/alerts
# Returns: any active quality degradation alerts

# Batch oversight
GET /api/overseer/batch/{batch_id}/report
# Returns: per-book quality summary for entire batch

# Pronunciation
GET /api/overseer/pronunciation-issues?book_id=42
# Returns: words flagged by pronunciation watchlist for this book
```

---

## Task 4: Production Overseer Frontend Page

### Implementation

Create `frontend/src/pages/ProductionOverseer.jsx`:

A comprehensive dashboard with these sections:

**1. Active Production Overview**
- Currently generating: book title, progress, estimated completion
- Queue depth: X books pending
- System status: memory usage, disk free, model health (canary result)

**2. Quality Scoreboard**
- Recent books table:
  | Book | Chapters | Gate 1 Pass | Gate 2 Grade | Gate 3 Grade | Issues | Status |
  | The Great Gatsby | 9 | 98% | A (3.8) | A | 2 | Ready |
  | Moby Dick | 135 | 94% | B (3.1) | B | 12 | Ready |
  | War & Peace | 361 | 91% | B (2.9) | C | 28 | Review |

- Color-coded: Green (A), Yellow (B), Orange (C), Red (F)
- Click any row to see detailed per-chapter breakdown

**3. Quality Trend Chart**
- Line chart showing rolling average of:
  - Gate 1 pass rate (should stay >95%)
  - Gate 2 average grade (should stay >3.0 = B)
  - Chunks regenerated per book (should stay low)
- Alert banner if trend is degrading

**4. Flagged Items**
- List of all chapters across all books that need attention:
  - Chapter grade C or F
  - Manual review notes present
  - Pronunciation warnings
  - ACX compliance failures
- Each item is actionable: "Regenerate", "Approve Override", "View Details"

**5. Export Readiness**
- For each completed book, show the export checklist:
  - [✓] All chapters generated
  - [✓] Gate 2: all chapters grade B or better
  - [✓] Gate 3: book passes
  - [✓] Mastering complete
  - [✗] ACX compliance: peak too hot on chapter 7
  - "Export" button enabled only when all checks pass

**6. System Health**
- Memory usage (current / threshold)
- Disk usage (used / free)
- Model status (loaded / last reload / canary result)
- Generation RTF (real-time factor, should be >1.0x)
- Uptime and last restart

### Navigation
Add "Production Overseer" to the main navigation sidebar, accessible from any page.

---

## Task 5: Export Verification Endpoint

### Implementation

```python
GET /api/overseer/book/{book_id}/export-verification

Returns:
{
    "book_id": 42,
    "title": "The Great Gatsby",
    "checks": [
        {"name": "all_chapters_generated", "passed": true, "detail": "9/9 chapters"},
        {"name": "gate2_minimum_grade", "passed": true, "detail": "All chapters grade B or better"},
        {"name": "gate3_passed", "passed": true, "detail": "Book grade: A"},
        {"name": "mastering_complete", "passed": true, "detail": "Loudness normalized, edges trimmed"},
        {"name": "acx_compliance", "passed": false, "detail": "Chapter 7 peak exceeds -3dB"},
        {"name": "metadata_complete", "passed": true, "detail": "Title, author, narrator set"},
        {"name": "chapter_markers_valid", "passed": true, "detail": "9 markers, all sequential"},
    ],
    "ready_for_export": false,
    "blockers": ["Chapter 7 peak exceeds -3dB — run mastering pipeline"],
    "recommendations": ["Consider regenerating chapter 3 (grade B, 2 warnings)"]
}
```

---

## Task 6: Tests

Create `tests/test_production_overseer.py`:
1. `test_manuscript_validation_detects_mojibake` — encoding artifacts block generation
2. `test_manuscript_validation_flags_proper_nouns` — high density flagged as info
3. `test_manuscript_validation_detects_poetry` — short lines flagged
4. `test_difficulty_score_calculation` — complex book scores higher
5. `test_quality_trend_stable` — consistent books show "stable" trend
6. `test_quality_trend_degrading` — declining pass rates show "degrading"
7. `test_export_checklist_blocks_on_failure` — ACX failure blocks export
8. `test_export_checklist_passes` — all checks pass enables export
9. `test_overseer_api_book_report` — API returns correct structure
10. `test_overseer_api_flagged_chapters` — only C/F grade chapters returned

All existing tests must still pass.

Rebuild frontend after all changes: `cd frontend && npm run build`

---

## Complete Prompt Roadmap Summary

| Prompt | Focus | Status |
|--------|-------|--------|
| 21 | Production hardening phase 1 (chunk validation, queue, QA, frontend) | Implemented |
| 22 | Progress indicators and heartbeats everywhere | Implemented |
| 23 | Sentence pause padding and voice defaults | Implemented |
| 24 | Gate 1: Per-chunk quality (STT, repeats, gibberish) | Implemented |
| 25 | Gate 2: Per-chapter quality (consistency, spectral, silence) | Implemented |
| 26 | Gate 3: Per-book quality (loudness, voice drift, ACX, mastering) | Implemented |
| 27 | Crash recovery, checkpointing, graceful shutdown | This prompt |
| 28 | Bulk generation hardening (100+ books) | This prompt |
| 29 | Qwen3-TTS model-specific mitigations | This prompt |
| 30 | Production overseer dashboard and reporting | This prompt |

After all 30 prompts: the system has three quality gates, automatic regeneration, crash recovery, bulk resilience, model-specific mitigations, and a comprehensive oversight dashboard. Every audiobook that passes through this pipeline meets professional standards.
